import { Alert, Button, Center, Loader, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconInbox } from "@tabler/icons-react";

import { ApiError } from "../api";

/**
 * 列表类查询的统一三态渲染。
 *
 * 用法：
 *   if (isLoading) return <ListLoading />;
 *   if (isError) return <ListError error={error} onRetry={refetch} />;
 *   if (!items.length) return <ListEmpty label="还没有服务器" hint="..." action={...} />;
 *
 * 之前若干页面只判断 isLoading，查询失败会静默退化成"空列表"，让人以为没数据。
 * 这里把三态做成同一套视觉语言，页面按需组合即可。
 */
export function ListLoading({ label = "读取中…" }: { label?: string }) {
  return (
    <Center py="xl">
      <Stack align="center" gap={8}>
        <Loader size="sm" color="steelblue" />
        <Text fz={11} c="dimmed">
          {label}
        </Text>
      </Stack>
    </Center>
  );
}

export function ListError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
  return (
    <Alert color="red" variant="light" icon={<IconAlertTriangle size={16} />} title="读取失败">
      <Stack gap={8} align="flex-start">
        <Text fz={12}>{detail}</Text>
        {onRetry && (
          <Button size="compact-xs" variant="default" onClick={onRetry}>
            重试
          </Button>
        )}
      </Stack>
    </Alert>
  );
}

export function ListEmpty({
  label,
  hint,
  action,
}: {
  label: string;
  hint?: string;
  action?: React.ReactNode;
}) {
  return (
    <Stack align="center" gap={6} py="xl">
      <IconInbox size={26} style={{ opacity: 0.35 }} />
      <Text fz={12.5} c="dimmed">
        {label}
      </Text>
      {hint && (
        <Text fz={11} c="dimmed" maw={520} ta="center">
          {hint}
        </Text>
      )}
      {action}
    </Stack>
  );
}
