import { Button, Group, Modal, Text } from "@mantine/core";
import { motion } from "motion/react";
import { createContext, useCallback, useContext, useMemo, useState } from "react";

import { EASE } from "../motion";

/**
 * 全局确认弹窗，取代散落各页的 window.confirm。
 *
 * 用法：const confirm = useConfirm(); if (await confirm({ title, message })) { ... }
 *
 * 放在 App 顶层 Provider 一次即可；返回 Promise<boolean>，点击遮罩/取消为 false。
 * 危险操作统一走这套 UI，风格与 Mantine Modal 一致，不再混入浏览器原生弹窗。
 */
interface ConfirmOptions {
  title?: string;
  message: string;
  confirmLabel?: string;
  cancelLabel?: string;
  danger?: boolean;
}

type ConfirmFn = (options: ConfirmOptions) => Promise<boolean>;

const ConfirmContext = createContext<ConfirmFn>(() => Promise.resolve(false));

export function ConfirmProvider({ children }: { children: React.ReactNode }) {
  const [state, setState] = useState<ConfirmOptions | null>(null);
  const [resolve, setResolve] = useState<((value: boolean) => void) | null>(null);

  const confirm = useCallback<ConfirmFn>((options) => {
    return new Promise<boolean>((done) => {
      setResolve(() => done);
      setState(options);
    });
  }, []);

  const finish = (value: boolean) => {
    resolve?.(value);
    setResolve(null);
    setState(null);
  };

  const value = useMemo(() => confirm, [confirm]);
  return (
    <ConfirmContext.Provider value={value}>
      {children}
      <Modal
        opened={state !== null}
        onClose={() => finish(false)}
        title={state?.title ?? "确认操作"}
        size="xs"
        centered
        transitionProps={{ transition: "pop", duration: 180 }}
      >
        {/* A danger confirmation should feel heavier than a normal one: the
            message settles in, the confirm button stays visually hot. */}
        <motion.div
          key={state?.message ?? "none"}
          initial={{ opacity: 0, y: 6 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.22, ease: EASE.snap }}
        >
          <Text fz={12.5} mb="md" style={{ whiteSpace: "pre-wrap" }}>
            {state?.message}
          </Text>
          <Group justify="flex-end" gap="xs">
            <Button size="compact-sm" variant="default" onClick={() => finish(false)}>
              {state?.cancelLabel ?? "取消"}
            </Button>
            <Button
              size="compact-sm"
              color={state?.danger === false ? "steelblue" : "red"}
              onClick={() => finish(true)}
            >
              {state?.confirmLabel ?? "确认"}
            </Button>
          </Group>
        </motion.div>
      </Modal>
    </ConfirmContext.Provider>
  );
}

export function useConfirm(): ConfirmFn {
  return useContext(ConfirmContext);
}
