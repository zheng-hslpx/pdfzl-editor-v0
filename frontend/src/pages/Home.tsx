import { Link } from "react-router-dom";
import { Badge, Card, Container, Grid, Group, Stack, Text, Title } from "@mantine/core";
import { CATEGORY_LABELS, TOOLS, type ToolCategory } from "../tools/registry";

const ORDER: ToolCategory[] = ["recommended", "organize", "convert", "edit", "extra"];

export function Home() {
  return (
    <Container size="lg" py="xl">
      <Title order={2} mb="xs">
        PDF 工具箱
      </Title>
      <Text c="dimmed" mb="xl">
        选择工具开始处理文件。架构参考 Stirling-PDF，数据全程本地处理，不上传外部服务。
      </Text>
      <Stack gap="xl">
        {ORDER.map((category) => {
          const tools = TOOLS.filter((t) => t.category === category);
          if (tools.length === 0) return null;
          return (
            <div key={category}>
              <Title order={4} mb="sm">
                {CATEGORY_LABELS[category]}
              </Title>
              <Grid>
                {tools.map((tool) => {
                  const Icon = tool.icon;
                  const card = (
                    <Card
                      withBorder
                      padding="lg"
                      radius="md"
                      style={{
                        opacity: tool.available ? 1 : 0.55,
                        cursor: tool.available ? "pointer" : "default",
                        height: "100%",
                      }}
                    >
                      <Group gap="sm" mb="xs" wrap="nowrap">
                        <Icon size={22} />
                        <Text fw={600}>{tool.name}</Text>
                        {!tool.available && (
                          <Badge size="xs" variant="light" color="gray">
                            开发中
                          </Badge>
                        )}
                      </Group>
                      <Text size="sm" c="dimmed">
                        {tool.desc}
                      </Text>
                    </Card>
                  );
                  return (
                    <Grid.Col key={tool.id} span={{ base: 12, sm: 6, md: 4 }}>
                      {tool.available ? (
                        <Link to={`/tool/${tool.id}`} style={{ textDecoration: "none" }}>
                          {card}
                        </Link>
                      ) : (
                        card
                      )}
                    </Grid.Col>
                  );
                })}
              </Grid>
            </div>
          );
        })}
      </Stack>
    </Container>
  );
}
