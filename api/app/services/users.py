"""Accounts and authentication. Local mode: scrypt passwords + HS256 JWT (demo and tests).
Cognito mode: users authenticate against a Cognito user pool; the API verifies the ID token and
maps the `cognito:groups` claim to a role. Roles: seeker, provider, admin."""

from __future__ import annotations

import hashlib
import hmac
import os
import time
from dataclasses import dataclass

import jwt

from shared.db import Database, new_id, now_iso

ROLES = ("seeker", "provider", "admin")


@dataclass
class Principal:
    id: str
    email: str
    name: str
    role: str
    org_id: str | None = None


class AuthError(Exception):
    pass


def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    h = hashlib.scrypt(pw.encode(), salt=salt, n=2**14, r=8, p=1)
    return f"scrypt${salt.hex()}${h.hex()}"


def check_password(pw: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, h = stored.split("$")
    got = hashlib.scrypt(pw.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1)
    return hmac.compare_digest(got.hex(), h)


class UserService:
    def __init__(self, db: Database):
        self.db = db

    def create(
        self,
        email: str,
        name: str,
        role: str,
        password: str | None = None,
        org_id: str | None = None,
        external_id: str | None = None,
    ) -> dict:
        if role not in ROLES:
            raise ValueError("invalid role")
        email = email.strip().lower()
        if self.by_email(email):
            raise AuthError("An account with that email already exists.")
        uid = new_id()
        self.db.run(
            "INSERT INTO users (id,email,name,role,password_hash,org_id,external_id,created_at) VALUES (?,?,?,?,?,?,?,?)",
            (
                uid,
                email,
                name,
                role,
                hash_password(password) if password else None,
                org_id,
                external_id,
                now_iso(),
            ),
        )
        return self.get(uid)

    def get(self, uid: str) -> dict | None:
        return self.db.one(
            "SELECT id,email,name,role,org_id,external_id,created_at,active FROM users WHERE id=?",
            (uid,),
        )

    def by_email(self, email: str) -> dict | None:
        return self.db.one("SELECT * FROM users WHERE email=?", (email.strip().lower(),))

    def list(self, role: str | None = None) -> list[dict]:
        sql, p = "SELECT id,email,name,role,org_id,created_at,active FROM users", []
        if role:
            sql += " WHERE role=?"
            p.append(role)
        return self.db.q(sql + " ORDER BY created_at DESC", p)

    def set_org(self, uid: str, org_id: str | None) -> None:
        self.db.run("UPDATE users SET org_id=? WHERE id=?", (org_id, uid))

    def authenticate(self, email: str, password: str) -> dict:
        u = self.by_email(email)
        if not u or not u["active"] or not check_password(password, u["password_hash"]):
            raise AuthError("Email or password is incorrect.")
        return u


class LocalAuth:
    mode = "local"

    def __init__(self, settings, users: UserService):
        self.s, self.users = settings, users

    def issue(self, u: dict) -> str:
        now = int(time.time())
        return jwt.encode(
            {
                "sub": u["id"],
                "email": u["email"],
                "name": u["name"],
                "role": u["role"],
                "org": u["org_id"],
                "iat": now,
                "exp": now + self.s.token_ttl_minutes * 60,
            },
            self.s.jwt_secret,
            algorithm="HS256",
        )

    def login(self, email: str, password: str) -> dict:
        u = self.users.authenticate(email, password)
        return {"token": self.issue(u), "user": _public(u)}

    def register_seeker(self, email: str, name: str, password: str) -> dict:
        if len(password) < 8:
            raise AuthError("Use a password with at least 8 characters.")
        u = self.users.create(email, name, "seeker", password)
        return {"token": self.issue(u), "user": _public(u)}

    def verify(self, token: str) -> Principal:
        try:
            c = jwt.decode(token, self.s.jwt_secret, algorithms=["HS256"])
        except jwt.PyJWTError as e:
            raise AuthError("Your session has expired. Log in again.") from e
        u = self.users.get(c["sub"])
        if not u or not u["active"]:
            raise AuthError("Account not found.")
        return Principal(u["id"], u["email"], u["name"], u["role"], u["org_id"])


class CognitoAuth:  # pragma: no cover - needs AWS
    """Cognito user pool. Login uses USER_PASSWORD_AUTH through the backend so the UI is identical in both modes.
    Groups `admin`, `provider`, `seeker` map to roles. Provider -> organization links live in the users table
    (set by an admin) so Cognito stays a pure identity store."""

    mode = "cognito"

    def __init__(self, settings, users: UserService):
        import boto3

        self.s, self.users = settings, users
        self.idp = boto3.client("cognito-idp", region_name=settings.aws_region)
        self.jwks = jwt.PyJWKClient(
            f"https://cognito-idp.{settings.aws_region}.amazonaws.com/{settings.cognito_user_pool_id}/.well-known/jwks.json"
        )

    def login(self, email: str, password: str) -> dict:
        try:
            r = self.idp.initiate_auth(
                ClientId=self.s.cognito_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={"USERNAME": email, "PASSWORD": password},
            )
        except self.idp.exceptions.NotAuthorizedException as e:
            raise AuthError("Email or password is incorrect.") from e
        token = r["AuthenticationResult"]["IdToken"]
        return {"token": token, "user": _public(self._user_from(self.verify(token)))}

    def register_seeker(self, email: str, name: str, password: str) -> dict:
        self.idp.admin_create_user(
            UserPoolId=self.s.cognito_user_pool_id,
            Username=email,
            MessageAction="SUPPRESS",
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "name", "Value": name},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        self.idp.admin_set_user_password(
            UserPoolId=self.s.cognito_user_pool_id,
            Username=email,
            Password=password,
            Permanent=True,
        )
        self.idp.admin_add_user_to_group(
            UserPoolId=self.s.cognito_user_pool_id, Username=email, GroupName="seeker"
        )
        return self.login(email, password)

    def create_provider_login(self, email: str, name: str) -> None:
        self.idp.admin_create_user(
            UserPoolId=self.s.cognito_user_pool_id,
            Username=email,
            UserAttributes=[
                {"Name": "email", "Value": email},
                {"Name": "name", "Value": name},
                {"Name": "email_verified", "Value": "true"},
            ],
        )
        self.idp.admin_add_user_to_group(
            UserPoolId=self.s.cognito_user_pool_id, Username=email, GroupName="provider"
        )

    def verify(self, token: str) -> Principal:
        try:
            key = self.jwks.get_signing_key_from_jwt(token).key
            c = jwt.decode(token, key, algorithms=["RS256"], audience=self.s.cognito_client_id)
        except jwt.PyJWTError as e:
            raise AuthError("Your session has expired. Log in again.") from e
        groups = c.get("cognito:groups", [])
        role = "admin" if "admin" in groups else "provider" if "provider" in groups else "seeker"
        u = self.users.by_email(c["email"]) or self.users.create(
            c["email"], c.get("name", c["email"]), role, None, external_id=c["sub"]
        )
        if u["role"] != role:
            self.users.db.run("UPDATE users SET role=? WHERE id=?", (role, u["id"]))
        return Principal(u["id"], u["email"], u.get("name") or c["email"], role, u.get("org_id"))

    def _user_from(self, p: Principal) -> dict:
        return {"id": p.id, "email": p.email, "name": p.name, "role": p.role, "org_id": p.org_id}


def _public(u: dict) -> dict:
    return {
        "id": u["id"],
        "email": u["email"],
        "name": u["name"],
        "role": u["role"],
        "org_id": u.get("org_id"),
    }


def build_auth(settings, users: UserService):
    return (
        CognitoAuth(settings, users)
        if settings.auth_mode == "cognito"
        else LocalAuth(settings, users)
    )
