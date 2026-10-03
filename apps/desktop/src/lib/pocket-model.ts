/**
 * The pocket model: a small language model running in the phone's own browser (web-llm, on
 * WebGPU), for when the PC is out of reach. Its files come from the PC (GET /api/pocket, then
 * /pocket/...), never from the internet, and stay in the browser's cache for this app.
 *
 * web-llm is several megabytes of script, so it is imported only when the pocket model is used.
 */

import type { AppConfig, MLCEngine } from "@mlc-ai/web-llm";

export type PocketModelInfo = { model: string; lib: string; available: boolean; size: number };

const INFO_KEY = "nova-pocket-model";

/** What the PC offers, remembered so the phone knows which model it has while the PC is away. */
export function rememberedInfo(): PocketModelInfo | null {
  try {
    const raw = localStorage.getItem(INFO_KEY);
    return raw ? (JSON.parse(raw) as PocketModelInfo) : null;
  } catch {
    return null;
  }
}

export function rememberInfo(info: PocketModelInfo): void {
  try {
    localStorage.setItem(INFO_KEY, JSON.stringify(info));
  } catch {
    // Storage blocked: the phone asks the PC again next time.
  }
}

function appConfig(info: PocketModelInfo): AppConfig {
  const origin = window.location.origin;
  return {
    model_list: [
      {
        model: `${origin}/pocket/${info.model}`,
        model_id: info.model,
        model_lib: `${origin}${info.lib}`,
        overrides: { context_window_size: 4096 },
      },
    ],
  };
}

export type PocketSupport = { ok: true; f16: boolean } | { ok: false; reason: string };

/**
 * Whether this phone can run a pocket model, and whether its graphics chip has 16-bit maths
 * (WebGPU shader-f16): without it the PC offers a smaller model in 32-bit maths instead.
 */
export async function pocketSupport(): Promise<PocketSupport> {
  if (typeof navigator === "undefined") return { ok: false, reason: "Not in a browser." };
  const gpu = (navigator as Navigator & { gpu?: { requestAdapter(): Promise<{ features: Set<string> } | null> } }).gpu;
  if (!gpu) {
    return {
      ok: false,
      reason: "This browser cannot run AI models. Chrome 121 or newer on Android 12 or newer can (it needs WebGPU).",
    };
  }
  const adapter = await gpu.requestAdapter().catch(() => null);
  if (!adapter) return { ok: false, reason: "This phone's graphics chip is not available to the browser (no WebGPU adapter)." };
  return { ok: true, f16: adapter.features.has("shader-f16") };
}

export async function pocketModelReady(info: PocketModelInfo): Promise<boolean> {
  const { hasModelInCache } = await import("@mlc-ai/web-llm");
  return hasModelInCache(info.model, appConfig(info)).catch(() => false);
}

let engine: Promise<MLCEngine> | null = null;

/**
 * Load the model onto the phone's graphics chip, downloading it from the PC first if it is not
 * cached yet. `onProgress` gets 0..1 and a description.
 */
export function loadPocketModel(info: PocketModelInfo, onProgress?: (share: number, text: string) => void): Promise<MLCEngine> {
  engine ??= import("@mlc-ai/web-llm")
    .then(({ CreateMLCEngine }) =>
      CreateMLCEngine(info.model, {
        appConfig: appConfig(info),
        initProgressCallback: (report) => onProgress?.(report.progress, report.text),
      }),
    )
    .catch((error) => {
      engine = null;
      throw error;
    });
  return engine;
}

/** Free the phone's graphics memory (after a download, or when the PC is back). */
export async function unloadPocketModel(): Promise<void> {
  const loaded = engine;
  engine = null;
  if (loaded) await (await loaded).unload().catch(() => {});
}

export async function deletePocketModel(info: PocketModelInfo): Promise<void> {
  await unloadPocketModel();
  const { deleteModelAllInfoInCache } = await import("@mlc-ai/web-llm");
  await deleteModelAllInfoInCache(info.model, appConfig(info));
}
