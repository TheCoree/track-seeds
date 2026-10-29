import { useCallback, useState } from "react";
import { toast } from "@/components/ui/toast";

/** Обёртка для действий: «занято» на кнопке, ошибка — во всплывающем сообщении. */
export function useAction() {
  const [busy, setBusy] = useState<string | null>(null);
  const run = useCallback(async <T,>(key: string, fn: () => Promise<T>, success?: string): Promise<T | undefined> => {
    setBusy(key);
    try {
      const result = await fn();
      if (success) toast.add({ title: success, type: "success" });
      return result;
    } catch (e) {
      toast.add({ title: "Не получилось", description: e instanceof Error ? e.message : String(e), type: "error", timeout: 9000 });
      return undefined;
    } finally {
      setBusy(null);
    }
  }, []);
  return { busy, run };
}
