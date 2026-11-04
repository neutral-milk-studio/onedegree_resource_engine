import { diffValues, diffWords } from "./wordDiff";

const text = (parts: { kind: string; text: string }[], kind: string) =>
  parts.filter((part) => part.kind === kind).map((part) => part.text.trim());

describe("diffWords", () => {
  it("finds no changes in identical text", () => {
    expect(diffWords("Open to all adults.", "Open to all adults.").changes).toBe(0);
  });

  it("marks only the words that changed on each side", () => {
    const { left, right } = diffWords("Income at or below 200% of poverty", "Income at or below 300% of poverty");
    expect(text(left, "removed")).toEqual(["200%"]);
    expect(text(right, "added")).toEqual(["300%"]);
  });

  it("shows an added item without marking the rest", () => {
    const { left, right } = diffWords("Montara, Moss Beach", "Montara, Moss Beach, La Honda");
    expect(text(left, "removed")).toEqual([]);
    expect(text(right, "added").join(" ")).toContain("La Honda");
  });

  it("ignores case and trailing punctuation", () => {
    expect(diffWords("Free.", "free").changes).toBe(0);
  });
});

describe("diffValues", () => {
  it("treats the same list in a different order as no difference", () => {
    const result = diffValues("El Granada, Moss Beach, Montara", "Montara, El Granada, Moss Beach");
    expect(result.changes).toBe(0);
    expect(text(result.right, "added")).toEqual([]);
  });

  it("counts a multi-word added item once and marks only that item", () => {
    const result = diffValues("Montara, Moss Beach", "Moss Beach, Montara, La Honda");
    expect(result.changes).toBe(1);
    expect(text(result.right, "added")).toEqual(["La Honda"]);
    expect(text(result.left, "removed")).toEqual([]);
  });

  it("counts removed and added items separately", () => {
    const result = diffValues("Montara, Moss Beach", "Montara, San Mateo County");
    expect(result.changes).toBe(2);
    expect(text(result.left, "removed")).toEqual(["Moss Beach"]);
    expect(text(result.right, "added")).toEqual(["San Mateo County"]);
  });

  it("splits hours on semicolons", () => {
    const result = diffValues("Mon 08:30 to 17:00; Wed 13:00 to 17:00", "Wed 13:00 to 17:00; Mon 08:30 to 12:00");
    expect(result.changes).toBe(2);
    expect(text(result.right, "added")).toEqual(["Mon 08:30 to 12:00"]);
  });

  it("uses word comparison for sentences that contain commas", () => {
    const result = diffValues(
      "Residents of El Granada, and people who work in the county every single day, can apply",
      "Residents of El Granada, and people who work in the city every single day, can apply",
    );
    expect(text(result.left, "removed")).toEqual(["county"]);
    expect(text(result.right, "added")).toEqual(["city"]);
  });

  it("counts a joined word change once", () => {
    expect(diffWords("Call the office", "Call the main front office").changes).toBe(1);
  });

  it.each([
    ["Income-eligible residents of El Granada, Montara, and Moss Beach", "Income-eligible residents of El Granada, Half Moon Bay, La Honda"],
    ["Montara, and Moss Beach", "Moss Beach; Montara"],
    ["Mon 08:30 to 17:00; and Wed 13:00 to 17:00", "Wed 13:00 to 17:00;Mon 08:30 to 12:00"],
    ["Free.", "No fee, ever"],
  ])("never changes the text it shows: %s", (before, after) => {
    const { left, right } = diffValues(before, after);
    expect(left.map((part) => part.text).join("")).toBe(before);
    expect(right.map((part) => part.text).join("")).toBe(after);
  });

  it("compares a long sentence word by word even if it has commas", () => {
    const result = diffValues("Residents of El Granada, Montara, and Moss Beach", "Residents of El Granada, Montara, La Honda, and Moss Beach");
    expect(text(result.left, "removed")).toEqual([]);
    expect(text(result.right, "added").join(" ")).toContain("La Honda");
  });
});
