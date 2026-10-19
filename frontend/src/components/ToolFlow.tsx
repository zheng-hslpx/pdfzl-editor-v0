import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Box,
  Button,
  Card,
  FileButton,
  Group,
  Paper,
  Stack,
  Stepper,
  Text,
  Title,
} from "@mantine/core";
import { IconDownload, IconFile, IconPlus, IconTrash, IconArrowUp, IconArrowDown } from "@tabler/icons-react";
import { useFiles, formatSize, type ManagedFile } from "../contexts/FileContext";
import { downloadBlob, extractError, type FileResult } from "../api/client";
import { PdfViewer } from "./PdfViewer";

export type ResultKind = "pdf" | "zip" | "image" | "text" | "json" | "docx";

export interface ToolFlowProps {
  /** Minimum number of files required to execute. */
  minFiles?: number;
  /** -1 = unlimited. */
  maxFiles?: number;
  accepts?: string;
  /** Settings panel rendered in step 2. */
  settings?: ReactNode;
  /** Caveats shown before executing (e.g. pdf2docx fidelity). */
  notices?: string[];
  /** Runs the tool; receives files in current order. */
  execute?: (files: ManagedFile[]) => Promise<FileResult>;
  resultKind: ResultKind;
  /** JSON variant: renders a custom panel instead of a file result. */
  executeJson?: (files: ManagedFile[]) => Promise<unknown>;
  renderJson?: (data: unknown) => ReactNode;
}

/**
 * Four-step tool flow (Files → Settings → Execute → Result), ported from
 * Stirling-PDF's createToolFlow. Each tool page only supplies its settings
 * panel and execute function.
 */
export function ToolFlow({
  minFiles = 1,
  maxFiles = -1,
  accepts,
  settings,
  notices,
  execute,
  resultKind,
  executeJson,
  renderJson,
}: ToolFlowProps) {
  const { files, addFiles, removeFile, moveFile } = useFiles();
  const [active, setActive] = useState(0);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<FileResult | null>(null);
  const [jsonData, setJsonData] = useState<unknown>(null);
  const [resultPreview, setResultPreview] = useState<ReactNode>(null);

  const canExecute = files.length >= minFiles && files.length <= (maxFiles === -1 ? Infinity : maxFiles);

  const run = async () => {
    setBusy(true);
    setError(null);
    try {
      if (executeJson) {
        const data = await executeJson(files);
        setJsonData(data);
        setResultPreview(renderJson ? renderJson(data) : null);
      } else if (execute) {
        const res = await execute(files);
        setResult(res);
        setResultPreview(renderPreview(res, resultKind));
      }
      setActive(3);
    } catch (err) {
      setError(await extractError(err));
    } finally {
      setBusy(false);
    }
  };

  const reset = () => {
    setResult(null);
    setJsonData(null);
    setResultPreview(null);
    setError(null);
    setActive(0);
  };

  return (
    <Stack gap="lg">
      <Stepper active={active} onStepClick={setActive} allowNextStepsSelect={false}>
        <Stepper.Step label="选择文件" description={`${files.length} 个文件`} />
        <Stepper.Step label="设置" description="工具参数" />
        <Stepper.Step label="执行" description="开始处理" />
        <Stepper.Step label="结果" description="下载 / 预览" />
      </Stepper>

      {active === 0 && (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Group>
              <FileButton onChange={addFiles} accept={accepts} multiple>
                {(props) => (
                  <Button {...props} leftSection={<IconPlus size={16} />}>
                    添加文件
                  </Button>
                )}
              </FileButton>
              <Text size="sm" c="dimmed">
                {maxFiles === -1 ? "数量不限" : `最多 ${maxFiles} 个`}
                {minFiles > 1 ? `，至少 ${minFiles} 个` : ""}
              </Text>
            </Group>
            {files.length === 0 ? (
              <Paper withBorder p="xl" radius="md" style={{ borderStyle: "dashed" }}>
                <Text ta="center" c="dimmed">
                  还没有选择文件，点击「添加文件」开始
                </Text>
              </Paper>
            ) : (
              <Stack gap="xs">
                {files.map((f, i) => (
                  <Paper key={f.id} withBorder p="xs" radius="sm">
                    <Group justify="space-between" wrap="nowrap">
                      <Group gap="xs" wrap="nowrap" style={{ minWidth: 0 }}>
                        <IconFile size={16} />
                        <Text size="sm" truncate style={{ minWidth: 0 }}>
                          {i + 1}. {f.name}
                        </Text>
                        <Text size="xs" c="dimmed" style={{ whiteSpace: "nowrap" }}>
                          {formatSize(f.size)}
                        </Text>
                      </Group>
                      <Group gap={4} wrap="nowrap">
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          onClick={() => moveFile(f.id, -1)}
                          disabled={i === 0}
                          title="上移"
                        >
                          <IconArrowUp size={14} />
                        </Button>
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          onClick={() => moveFile(f.id, 1)}
                          disabled={i === files.length - 1}
                          title="下移"
                        >
                          <IconArrowDown size={14} />
                        </Button>
                        <Button
                          size="compact-xs"
                          variant="subtle"
                          color="red"
                          onClick={() => removeFile(f.id)}
                          title="移除"
                        >
                          <IconTrash size={14} />
                        </Button>
                      </Group>
                    </Group>
                  </Paper>
                ))}
              </Stack>
            )}
            <Group justify="flex-end">
              <Button onClick={() => setActive(1)} disabled={files.length === 0}>
                下一步：设置
              </Button>
            </Group>
          </Stack>
        </Card>
      )}

      {active === 1 && (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Box>{settings ?? <Text c="dimmed">此工具无需额外设置。</Text>}</Box>
            {notices?.map((n) => (
              <Alert key={n} color="yellow" title="注意">
                {n}
              </Alert>
            ))}
            <Group justify="space-between">
              <Button variant="default" onClick={() => setActive(0)}>
                上一步
              </Button>
              <Button onClick={() => setActive(2)} disabled={!canExecute}>
                下一步：执行
              </Button>
            </Group>
          </Stack>
        </Card>
      )}

      {active === 2 && (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            <Text>
              即将处理 {files.length} 个文件：
              {files.map((f) => f.name).join("、")}
            </Text>
            {error && (
              <Alert color="red" title="处理失败">
                {error}
              </Alert>
            )}
            <Group justify="space-between">
              <Button variant="default" onClick={() => setActive(1)} disabled={busy}>
                上一步
              </Button>
              <Button onClick={run} loading={busy} disabled={!canExecute}>
                开始处理
              </Button>
            </Group>
          </Stack>
        </Card>
      )}

      {active === 3 && (result || jsonData !== null) && (
        <Card withBorder padding="lg" radius="md">
          <Stack gap="md">
            {result ? (
              <Alert color="teal" title="处理完成">
                {result.filename}
              </Alert>
            ) : (
              <Alert color="teal" title="查询完成" />
            )}
            <Group>
              {result && (
                <Button
                  leftSection={<IconDownload size={16} />}
                  onClick={() => downloadBlob(result.blob, result.filename)}
                >
                  下载文件
                </Button>
              )}
              <Button variant="default" onClick={reset}>
                处理其他文件
              </Button>
            </Group>
            {resultPreview}
          </Stack>
        </Card>
      )}
    </Stack>
  );
}

function renderPreview(result: FileResult, kind: ResultKind): ReactNode {
  switch (kind) {
    case "pdf":
      return <PdfPreview blob={result.blob} />;
    case "image":
      return <ImagePreview blob={result.blob} />;
    case "text":
    case "json":
      return <TextPreview blob={result.blob} />;
    default:
      return (
        <Text size="sm" c="dimmed">
          该格式暂不支持内嵌预览，请下载后查看。
        </Text>
      );
  }
}

function PdfPreview({ blob }: { blob: Blob }) {
  const [data, setData] = useState<Uint8Array | null>(null);
  useEffect(() => {
    let cancelled = false;
    void blob.arrayBuffer().then((buf) => {
      if (!cancelled) setData(new Uint8Array(buf));
    });
    return () => {
      cancelled = true;
    };
  }, [blob]);
  if (!data) return null;
  return (
    <Box>
      <Title order={5} mb="xs">
        预览
      </Title>
      <Paper withBorder p="sm" radius="md" style={{ background: "#f8f9fa" }}>
        <PdfViewer data={data} />
      </Paper>
    </Box>
  );
}

function ImagePreview({ blob }: { blob: Blob }) {
  const [url, setUrl] = useState<string | null>(null);
  useEffect(() => {
    const u = URL.createObjectURL(blob);
    setUrl(u);
    return () => URL.revokeObjectURL(u);
  }, [blob]);
  if (!url) return null;
  return <img src={url} alt="result" style={{ maxWidth: "100%", border: "1px solid #ddd" }} />;
}

function TextPreview({ blob }: { blob: Blob }) {
  const [text, setText] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void blob.text().then((t) => {
      if (!cancelled) setText(t);
    });
    return () => {
      cancelled = true;
    };
  }, [blob]);
  if (text === null) return null;
  return (
    <Paper withBorder p="sm" radius="md" style={{ maxHeight: 320, overflow: "auto" }}>
      <Text component="pre" size="xs" style={{ whiteSpace: "pre-wrap" }}>
        {text}
      </Text>
    </Paper>
  );
}
