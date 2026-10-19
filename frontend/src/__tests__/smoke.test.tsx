import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { describe, expect, it } from "vitest";
import { TOOLS, getTool } from "../tools/registry";
import { FileProvider } from "../contexts/FileContext";
import { ToolFlow } from "../components/ToolFlow";

describe("tool registry", () => {
  it("has unique tool ids", () => {
    const ids = TOOLS.map((t) => t.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it("resolves tools by id", () => {
    expect(getTool("merge")?.name).toBe("合并 PDF");
    expect(getTool("nope")).toBeUndefined();
  });

  it("every available tool has a lazy component", () => {
    for (const tool of TOOLS.filter((t) => t.available)) {
      expect(tool.component).toBeDefined();
    }
  });
});

describe("ToolFlow", () => {
  it("renders the four-step stepper", () => {
    render(
      <MantineProvider>
        <MemoryRouter>
          <FileProvider>
            <ToolFlow resultKind="pdf" execute={async () => ({ blob: new Blob(), filename: "x.pdf" })} />
          </FileProvider>
        </MemoryRouter>
      </MantineProvider>,
    );
    expect(screen.getByText("选择文件")).toBeDefined();
    expect(screen.getByText("设置")).toBeDefined();
    expect(screen.getByText("执行")).toBeDefined();
    expect(screen.getByText("结果")).toBeDefined();
  });
});
