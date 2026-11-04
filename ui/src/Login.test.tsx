import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { vi } from "vitest";

import { Login } from "./Login";

describe("Login", () => {
  it("shows an error for wrong credentials and doesn't sign in", async () => {
    const onSignedIn = vi.fn();
    const user = userEvent.setup();
    render(<Login onSignedIn={onSignedIn} />);

    await user.type(screen.getByLabelText("Email"), "sam.rivera@example.org");
    await user.type(screen.getByLabelText("Password"), "wrong");
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("isn't right");
    expect(onSignedIn).not.toHaveBeenCalled();
  });

  it("signs in a demo account with the reviewer's name", async () => {
    const onSignedIn = vi.fn();
    const user = userEvent.setup();
    render(<Login onSignedIn={onSignedIn} />);

    await user.click(screen.getByRole("button", { name: "Use Sam Rivera" }));
    await user.click(screen.getByRole("button", { name: "Sign in" }));

    await vi.waitFor(() => expect(onSignedIn).toHaveBeenCalledWith(expect.objectContaining({ name: "Sam Rivera" })));
  });
});
