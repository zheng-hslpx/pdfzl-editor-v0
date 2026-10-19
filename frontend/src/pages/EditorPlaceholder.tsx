import { Link } from "react-router-dom";
import { Alert, Container, Text, Title } from "@mantine/core";

export function EditorPlaceholder() {
  return (
    <Container size="sm" py="xl">
      <Title order={2}>可视化编辑器</Title>
      <Text c="dimmed" mb="md">
        编辑器将在 M5 里程碑上线：打开 PDF 后可直接修改文本、添加文字/图片、涂抹内容，
        并支持保存与另存为。
      </Text>
      <Alert color="yellow" title="开发中">
        当前版本尚未提供编辑功能。<Link to="/">返回首页</Link>
      </Alert>
    </Container>
  );
}
