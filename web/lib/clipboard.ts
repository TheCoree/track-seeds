import { toast } from "@/components/ui/toast";

/** Скопировать; по http://192.168… Clipboard API нет — через выделение текста. */
export async function copyText(text: string, done = "Скопировано") {
  try {
    if (navigator.clipboard && window.isSecureContext) await navigator.clipboard.writeText(text);
    else {
      const area = document.createElement("textarea");
      area.value = text;
      area.setAttribute("readonly", "");
      area.style.position = "fixed";
      area.style.opacity = "0";
      document.body.appendChild(area);
      area.select();
      const ok = document.execCommand("copy");
      area.remove();
      if (!ok) throw new Error("браузер не дал скопировать");
    }
    toast.add({ title: done, type: "success" });
  } catch (e) {
    toast.add({ title: "Не скопировалось — выделите и скопируйте вручную", description: e instanceof Error ? e.message : String(e), type: "error" });
  }
}

/** «Chrome · Windows» из User-Agent — чтобы узнать устройство в списке сессий. */
export function deviceName(agent: string | null): string {
  if (!agent) return "Неизвестное устройство";
  const os = /iPhone/.test(agent) ? "iPhone" : /iPad/.test(agent) ? "iPad" : /Android/.test(agent) ? "Android" : /Windows/.test(agent) ? "Windows" : /Mac OS/.test(agent) ? "Mac" : /Linux/.test(agent) ? "Linux" : "";
  const browser = /YaBrowser/.test(agent)
    ? "Яндекс Браузер"
    : /Edg\//.test(agent)
      ? "Edge"
      : /Firefox\//.test(agent)
        ? "Firefox"
        : /Chrome\//.test(agent)
          ? "Chrome"
          : /Safari\//.test(agent)
            ? "Safari"
            : /Scriptable/.test(agent)
              ? "Scriptable"
              : "Браузер";
  return os ? `${browser} · ${os}` : browser;
}
