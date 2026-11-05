import { render, screen } from "@testing-library/react";

import { ResultsMap } from "./ResultsMap";

describe("ResultsMap", () => {
  it("renders nothing when no result has coordinates", () => {
    const { container } = render(<ResultsMap points={[]} />);
    expect(container).toBeEmptyDOMElement();
  });

  it("renders a labeled map container for the given points", async () => {
    render(<ResultsMap points={[{ id: "a", name: "Food Bank", latitude: 37.8, longitude: -122.27 }]} />);
    expect(await screen.findByRole("group", { name: "Map of 1 result" })).toBeInTheDocument();
  });

  it("pluralizes the label for more than one point", async () => {
    render(
      <ResultsMap
        points={[
          { id: "a", name: "Food Bank", latitude: 37.8, longitude: -122.27 },
          { id: "b", name: "Shelter", latitude: 37.81, longitude: -122.28 },
        ]}
      />,
    );
    expect(await screen.findByRole("group", { name: "Map of 2 results" })).toBeInTheDocument();
  });
});
