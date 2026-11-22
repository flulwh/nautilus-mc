import { Alert, Button, Center, Stack, Text } from "@mantine/core";
import { IconAlertTriangle, IconInbox } from "@tabler/icons-react";
import { motion } from "motion/react";

import { ApiError } from "../api";
import { EASE, cardVariants, staggerContainer, staggerItem } from "../motion";

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

/** A shimmer bar standing in for a line of content. */
export function Skeleton({ h = 13, w = "100%" }: { h?: number; w?: number | string }) {
  return <div className="skeleton" style={{ height: h, width: w }} />;
}

/**
 * A skeleton shaped like a table: a header rule plus a few rows of varying
 * length, so the page does not jump when the real data lands.
 */
export function SkeletonRows({ rows = 5 }: { rows?: number }) {
  return (
    <Stack gap={0} aria-busy="true" aria-live="polite">
      <div
        style={{
          display: "grid",
          gridTemplateColumns: "2fr 1fr 1fr 1fr",
          gap: 12,
          padding: "6px 8px",
          borderBottom: "1px solid var(--line)",
        }}
      >
        {[0, 1, 2, 3].map((i) => (
          <Skeleton key={i} h={9} w="60%" />
        ))}
      </div>
      {Array.from({ length: rows }, (_, r) => (
        <div
          key={r}
          style={{
            display: "grid",
            gridTemplateColumns: "2fr 1fr 1fr 1fr",
            gap: 12,
            padding: "9px 8px",
            borderBottom: "1px solid var(--line)",
          }}
        >
          <Skeleton h={13} w={`${88 - r * 7}%`} />
          <Skeleton h={13} w="70%" />
          <Skeleton h={13} w="55%" />
          <Skeleton h={13} w="45%" />
        </div>
      ))}
    </Stack>
  );
}

export function ListLoading({ label = "读取中…" }: { label?: string }) {
  return (
    <motion.div
      initial={{ opacity: 0 }}
      animate={{ opacity: 1 }}
      transition={{ duration: 0.2, ease: EASE.snap }}
      aria-busy="true"
    >
      <Center py="xl">
        <Stack align="center" gap={8}>
          {/* Three dots that light up in sequence — no spinner ring. */}
          <div style={{ display: "flex", gap: 5 }}>
            {[0, 1, 2].map((i) => (
              <motion.span
                key={i}
                style={{
                  width: 6,
                  height: 6,
                  background: "var(--accent)",
                  borderRadius: 1,
                }}
                animate={{ opacity: [0.25, 1, 0.25], y: [0, -3, 0] }}
                transition={{
                  duration: 0.9,
                  repeat: Infinity,
                  ease: "easeInOut",
                  delay: i * 0.13,
                }}
              />
            ))}
          </div>
          <Text fz={11} c="dimmed">
            {label}
          </Text>
        </Stack>
      </Center>
    </motion.div>
  );
}

export function ListError({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  const detail = error instanceof ApiError ? error.detail : error instanceof Error ? error.message : String(error);
  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: EASE.snap }}
    >
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
    </motion.div>
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
    <motion.div
      variants={staggerContainer(0.06)}
      initial="hidden"
      animate="show"
    >
      <Stack align="center" gap={6} py="xl">
        <motion.div variants={staggerItem}>
          <IconInbox size={26} style={{ opacity: 0.35 }} />
        </motion.div>
        <motion.div variants={staggerItem}>
          <Text fz={12.5} c="dimmed">
            {label}
          </Text>
        </motion.div>
        {hint && (
          <motion.div variants={staggerItem}>
            <Text fz={11} c="dimmed" maw={520} ta="center">
              {hint}
            </Text>
          </motion.div>
        )}
        {action && (
          <motion.div variants={staggerItem}>
            {action}
          </motion.div>
        )}
      </Stack>
    </motion.div>
  );
}

/**
 * A card that settles into place and lifts slightly on hover. Used for the
 * dashboard stat strip and the system info panels.
 */
export function LiftCard({ children, className = "" }: { children: React.ReactNode; className?: string }) {
  return (
    <motion.div
      className={`card-lift ${className}`}
      variants={cardVariants}
      style={{ border: "1px solid var(--line)", background: "var(--panel)" }}
    >
      {children}
    </motion.div>
  );
}
