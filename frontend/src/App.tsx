import { useEffect, useState } from "react";
import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Alert, Badge, Card, Container, Text, Title } from "@mantine/core";
import { checkHealth } from "./api/client";

function Home() {
  const [health, setHealth] = useState("检查中…");
  const [ok, setOk] = useState<boolean | null>(null);

  useEffect(() => {
    checkHealth()
      .then((r) => {
        setHealth(r.status);
        setOk(true);
      })
      .catch(() => {
        setHealth("后端未连接");
        setOk(false);
      });
  }, []);

  return (
    <Container size="sm" py="xl">
      <Title order={1}>PDF Editor</Title>
      <Text c="dimmed" mb="lg">
        架构模式参考 Stirling-PDF 的轻量级 PDF 编辑器
      </Text>
      <Card withBorder padding="lg" radius="md">
        <Text fw={600} mb="xs">
          后端状态
        </Text>
        <Badge color={ok ? "teal" : ok === null ? "gray" : "red"} variant="light">
          {health}
        </Badge>
        {ok === false && (
          <Alert color="yellow" title="提示" mt="md">
            请先启动后端：cd backend &amp;&amp; uvicorn app.main:app --port 8000
          </Alert>
        )}
      </Card>
    </Container>
  );
}

export function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
      </Routes>
    </BrowserRouter>
  );
}
