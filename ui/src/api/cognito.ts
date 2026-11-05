// Real per-specialist sign-in against the AWS deploy's Cognito user pool, instead of one
// shared token. The pool's "web" app client has no secret (infra/terraform/data.tf:
// generate_secret = false, ALLOW_USER_PASSWORD_AUTH) -- it's a public SPA client meant
// for exactly this: a plain, unauthenticated HTTPS call to Cognito's API, no AWS SDK
// needed. The ID token it returns is sent as the bearer token; the API verifies it
// (api/app/cognito_auth.py) and attributes edits to the real signed-in email.
import { ApiError, type Session } from "./client";

const REGION: string = import.meta.env.VITE_AWS_REGION ?? "us-west-2";
const CLIENT_ID: string | undefined = import.meta.env.VITE_COGNITO_CLIENT_ID;

/** True once the deploy has been given a Cognito client id (infra/terraform output
 * `cognito_client_id`); false in local/demo use, where the shared token is used instead. */
export const cognitoConfigured = Boolean(CLIENT_ID);

interface InitiateAuthResponse {
  AuthenticationResult?: { IdToken?: string };
  __type?: string;
  message?: string;
}

export async function cognitoSignIn(email: string, password: string): Promise<Session> {
  let response: Response;
  try {
    response = await fetch(`https://cognito-idp.${REGION}.amazonaws.com/`, {
      method: "POST",
      headers: {
        "Content-Type": "application/x-amz-json-1.1",
        "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth",
      },
      body: JSON.stringify({
        AuthFlow: "USER_PASSWORD_AUTH",
        ClientId: CLIENT_ID,
        AuthParameters: { USERNAME: email, PASSWORD: password },
      }),
    });
  } catch {
    throw new ApiError(0, "Can't reach Cognito. Check your connection and try again.");
  }
  const body = (await response.json().catch(() => ({}))) as InitiateAuthResponse;
  if (!response.ok) {
    const wrongCredentials = body.__type === "NotAuthorizedException" || body.__type === "UserNotFoundException";
    throw new ApiError(
      wrongCredentials ? 401 : response.status,
      wrongCredentials ? "That email or password isn't right." : body.message ?? "Couldn't sign you in. Try again.",
    );
  }
  const idToken = body.AuthenticationResult?.IdToken;
  if (!idToken) throw new ApiError(401, "Couldn't sign you in. Try again.");
  return { email, name: email.split("@")[0], token: idToken };
}
