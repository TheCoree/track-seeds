import { api } from "./api";
import type { Crusher } from "./types";

/** Тело PUT /crushers/{id}: только то, что меняется на месте (имя и зоны вшиты в программу). */
export function crusherBody(crusher: Crusher, patch: Partial<Pick<Crusher, "enabled" | "notify" | "camera" | "seeds" | "speakers">> = {}) {
  const next = { ...crusher, ...patch };
  return { enabled: next.enabled, notify: next.notify, camera: next.camera, seeds: { ...next.seeds, samples: crusher.seeds.samples }, speakers: next.speakers };
}

export const saveCrusher = (crusher: Crusher, patch: Parameters<typeof crusherBody>[1]) => api.put<Crusher>(`/crushers/${crusher.id}`, crusherBody(crusher, patch));

export const sampleUrl = (crusher: Crusher, sampleId: string) => `/api/crushers/${crusher.id}/samples/${sampleId}.jpg`;
