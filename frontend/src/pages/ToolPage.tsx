import { Suspense } from "react";
import { Link, useParams } from "react-router-dom";
import { Alert, Container, Loader, Stack, Text, Title } from "@mantine/core";
import { getTool } from "../tools/registry";

export function ToolPage() {
  const { toolId } = useParams();
  const tool = getTool(toolId);

  if (!tool) {
    return (
      <Container size="sm" py="xl">
        <Alert color="red" title="工具不存在">
          没有找到 ID 为 “{toolId}” 的工具。<Link to="/">返回首页</Link>
        </Alert>
      </Container>
    );
  }

  if (!tool.available || !tool.component) {
    return (
      <Container size="sm" py="xl">
        <Alert color="yellow" title={`${tool.name} 开发中`}>
          该工具正在开发中，将在后续里程碑上线。<Link to="/">返回首页</Link>
        </Alert>
      </Container>
    );
  }

  const ToolComponent = tool.component;
  return (
    <Container size="md" py="xl">
      <Stack gap="lg">
        <div>
          <Title order={2}>{tool.name}</Title>
          <Text c="dimmed" mt={4}>
            {tool.desc}
          </Text>
        </div>
        <Suspense fallback={<Loader />}>
          <ToolComponent />
        </Suspense>
      </Stack>
    </Container>
  );
}
