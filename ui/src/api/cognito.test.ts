import { afterEach, vi } from "vitest";

import { ApiError } from "./client";
import { cognitoSignIn } from "./cognito";

function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("cognitoSignIn", () => {
  it("calls Cognito's InitiateAuth with USER_PASSWORD_AUTH and returns the ID token as the session", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { AuthenticationResult: { IdToken: "the-id-token" } }));
    vi.stubGlobal("fetch", fetchMock);

    const session = await cognitoSignIn("specialist@example.org", "correct horse battery staple");

    expect(session).toEqual({ email: "specialist@example.org", name: "specialist", token: "the-id-token" });
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toMatch(/^https:\/\/cognito-idp\..+\.amazonaws\.com\/$/);
    expect(init.headers).toMatchObject({ "X-Amz-Target": "AWSCognitoIdentityProviderService.InitiateAuth" });
    const body = JSON.parse(init.body as string) as { AuthFlow: string; AuthParameters: { USERNAME: string; PASSWORD: string } };
    expect(body.AuthFlow).toBe("USER_PASSWORD_AUTH");
    expect(body.AuthParameters).toEqual({ USERNAME: "specialist@example.org", PASSWORD: "correct horse battery staple" });
  });

  it("maps a wrong-password response to a friendly 401", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(400, { __type: "NotAuthorizedException", message: "Incorrect username or password." })));

    await expect(cognitoSignIn("a@b.com", "wrong")).rejects.toMatchObject({
      status: 401,
      message: "That email or password isn't right.",
    } satisfies Partial<ApiError>);
  });

  it("surfaces a network failure as a clear error, not a crash", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("fetch failed")));

    await expect(cognitoSignIn("a@b.com", "x")).rejects.toMatchObject({ status: 0 });
  });

  it("rejects when Cognito reports success but no token came back", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(200, {})));

    await expect(cognitoSignIn("a@b.com", "x")).rejects.toMatchObject({ status: 401 });
  });
});
