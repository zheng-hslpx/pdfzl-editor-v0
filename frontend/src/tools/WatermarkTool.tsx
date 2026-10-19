import { useState } from "react";
import { ColorInput, NumberInput, TextInput } from "@mantine/core";
import { ToolFlow } from "../components/ToolFlow";
import { postFiles } from "../api/client";

export default function WatermarkTool() {
  const [text, setText] = useState("内部资料");
  const [opacity, setOpacity] = useState<number | string>(0.3);
  const [rotation, setRotation] = useState<number | string>(45);
  const [fontSize, setFontSize] = useState<number | string>(48);
  const [color, setColor] = useState("#888888");

  return (
    <ToolFlow
      minFiles={1}
      maxFiles={1}
      accepts=".pdf,application/pdf"
      resultKind="pdf"
      settings={
        <div style={{ display: "grid", gap: 12 }}>
          <TextInput
            label="水印文字"
            value={text}
            onChange={(e) => setText(e.currentTarget.value)}
            required
          />
          <NumberInput
            label="透明度（0-1）"
            value={opacity}
            onChange={setOpacity}
            min={0.05}
            max={1}
            step={0.05}
            decimalScale={2}
          />
          <NumberInput
            label="旋转角度"
            value={rotation}
            onChange={setRotation}
            min={0}
            max={360}
          />
          <NumberInput
            label="字号"
            value={fontSize}
            onChange={setFontSize}
            min={6}
            max={200}
          />
          <ColorInput
            label="颜色"
            value={color}
            onChange={setColor}
            format="hex"
          />
        </div>
      }
      execute={async (managed) => {
        const form = new FormData();
        form.append("fileInput", managed[0]!.file, managed[0]!.name);
        form.append("text", text);
        form.append("opacity", String(opacity));
        form.append("rotation", String(rotation));
        form.append("fontSize", String(fontSize));
        form.append("color", color);
        return postFiles("/misc/watermark", form);
      }}
    />
  );
}
