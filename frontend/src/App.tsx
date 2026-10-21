import { useEffect, useState } from "react";
import { createBrowserRouter, Link, Outlet, RouterProvider } from "react-router-dom";
import { AppShell, Badge, Burger, Group, Text, Title } from "@mantine/core";
import { useDisclosure } from "@mantine/hooks";
import { FileProvider } from "./contexts/FileContext";
import { checkHealth } from "./api/client";
import { Home } from "./pages/Home";
import { ToolPage } from "./pages/ToolPage";
import { EditorPage } from "./pages/EditorPage";

function Shell() {
  const [opened, { toggle }] = useDisclosure();
  const [backend, setBackend] = useState<"ok" | "down" | "checking">("checking");

  useEffect(() => {
    checkHealth()
      .then(() => setBackend("ok"))
      .catch(() => setBackend("down"));
  }, []);

  return (
    <AppShell
      header={{ height: 56 }}
      navbar={{ width: 220, breakpoint: "sm", collapsed: { mobile: !opened } }}
      padding="md"
    >
      <AppShell.Header>
        <Group h="100%" px="md" justify="space-between">
          <Group>
            <Burger opened={opened} onClick={toggle} hiddenFrom="sm" size="sm" />
            <Title order={4}>PDF Editor</Title>
          </Group>
          <Badge
            color={backend === "ok" ? "teal" : backend === "down" ? "red" : "gray"}
            variant="light"
          >
            {backend === "ok" ? "后端已连接" : backend === "down" ? "后端未连接" : "检查中…"}
          </Badge>
        </Group>
      </AppShell.Header>

      <AppShell.Navbar p="md">
        <Text size="xs" c="dimmed" fw={700} mb="xs">
          导航
        </Text>
        <Text
          component={Link}
          to="/"
          size="sm"
          style={{ display: "block", marginBottom: 8, textDecoration: "none" }}
        >
          全部工具
        </Text>
        <Text
          component={Link}
          to="/editor"
          size="sm"
          style={{ display: "block", textDecoration: "none" }}
        >
          可视化编辑器
        </Text>
      </AppShell.Navbar>

      <AppShell.Main>
        <Outlet />
      </AppShell.Main>
    </AppShell>
  );
}

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Home /> },
      { path: "tool/:toolId", element: <ToolPage /> },
      { path: "editor", element: <EditorPage /> },
      { path: "*", element: <Home /> },
    ],
  },
]);

export function App() {
  return (
    <FileProvider>
      <RouterProvider router={router} />
    </FileProvider>
  );
}
