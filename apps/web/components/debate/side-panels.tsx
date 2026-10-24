"use client";

// 그리드 블록의 자료(MaterialList + MarkdownViewer)와 AI 어시스턴트(AssistantPanel).
// 자료는 키워드가 정해진 뒤에만 열리고, 어시스턴트는 열린 자료만 찾는다. 반짝이·로봇 아이콘은 쓰지 않는다.
import { ChevronLeft, ChevronRight, Copy, FileText, Lock } from "lucide-react";
import { useState } from "react";

import { Button, toast } from "@/components/ui";

import { AssistantMarkdown, Markdown } from "./markdown";
import type { AssistantEntry, Material, Materials } from "./types";

export function MaterialsPanel({
  materials,
  onOpen,
}: {
  materials: Materials;
  /** 본문을 읽는다(열람 기록). 실패하면 null */
  onOpen: (materialId: string) => Promise<Material | null>;
}) {
  const [doc, setDoc] = useState<Material | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  if (materials.locked)
    return (
      <div className="flex flex-col items-center gap-2 px-6 py-16 text-center">
        <Lock size={20} className="text-muted" aria-hidden />
        <p className="text-body text-muted">키워드가 정해지면 자료가 열립니다.</p>
      </div>
    );

  if (doc)
    return (
      <article className="flex flex-col gap-3 px-6 py-4">
        <button
          type="button"
          onClick={() => setDoc(null)}
          className="inline-flex items-center gap-1 self-start text-footnote text-accent"
        >
          <ChevronLeft size={16} aria-hidden /> 자료 목록
        </button>
        <Markdown className="max-w-[68ch] text-body leading-relaxed">{doc.content}</Markdown>
      </article>
    );

  if (materials.materials.length === 0)
    return <p className="px-6 py-16 text-center text-muted">이 키워드에는 준비된 자료가 없어요</p>;

  return (
    <div className="flex flex-col pb-2">
      <p className="px-6 pt-4 pb-1 text-footnote font-semibold text-muted">
        「{materials.keyword}」 자료
      </p>
      <ul className="flex flex-col divide-y divide-line">
        {materials.materials.map((m) => (
          <li key={m.material_id}>
            <button
              type="button"
              disabled={loading !== null}
              onClick={async () => {
                setLoading(m.material_id);
                const body = await onOpen(m.material_id);
                setLoading(null);
                if (body) setDoc(body);
              }}
              className="flex w-full items-center gap-3 px-6 py-3.5 text-left hover:bg-surface-sunken"
            >
              <FileText size={16} className="shrink-0 text-muted" aria-hidden />
              <span className="flex-1 text-body">{m.title}</span>
              <ChevronRight size={16} className="text-muted" aria-hidden />
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function AssistantPanel({
  entries,
  materials,
  locked,
  onAsk,
}: {
  entries: AssistantEntry[];
  materials: Materials;
  locked: boolean;
  /** 요청을 보낸다. 오류 문구 또는 null */
  onAsk: (prompt: string) => Promise<string | null>;
}) {
  const [prompt, setPrompt] = useState("");
  const [waiting, setWaiting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const titles = new Map(materials.materials.map((m) => [m.material_id, m.title]));

  const ask = async () => {
    const text = prompt.trim();
    if (!text || waiting) return;
    setWaiting(text);
    setError(null);
    const result = await onAsk(text);
    setWaiting(null);
    if (result) setError(result);
    else setPrompt("");
  };

  return (
    <div className="flex h-full flex-col">
      <p className="px-6 pt-4 text-footnote text-muted">
        열린 자료만 찾아 답합니다. 토론방의 AI 동료는 이 대화를 보지 않습니다.
      </p>
      <ol className="flex flex-1 flex-col divide-y divide-line overflow-y-auto px-6">
        {entries.map((entry) => (
          <li key={entry.call_id} className="flex flex-col gap-3 py-5">
            <p className="border-l-2 border-line pl-3 text-footnote break-words text-muted">
              {entry.request}
            </p>
            <AssistantMarkdown className="text-body leading-relaxed">
              {entry.response}
            </AssistantMarkdown>
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {entry.cited_material_ids.map((id) => (
                <span
                  key={id}
                  className="inline-flex items-center gap-1 rounded-sm bg-surface-sunken px-2 py-0.5 text-caption text-muted"
                >
                  <FileText size={12} aria-hidden />
                  {titles.get(id) ?? id}
                </span>
              ))}
              <button
                type="button"
                onClick={() => {
                  void navigator.clipboard?.writeText(entry.response);
                  toast("복사했어요");
                }}
                className="ml-auto inline-flex items-center gap-1 text-caption text-accent"
              >
                <Copy size={12} aria-hidden /> 복사
              </button>
            </div>
          </li>
        ))}
        {waiting && (
          <li className="flex flex-col gap-3 py-5">
            <p className="border-l-2 border-line pl-3 text-footnote break-words text-muted">
              {waiting}
            </p>
            <p className="text-body text-muted">자료를 찾는 중…</p>
          </li>
        )}
      </ol>
      <div className="flex flex-col gap-2 border-t border-line px-6 py-3">
        {error && (
          <p role="alert" className="text-footnote text-danger">
            {error}
          </p>
        )}
        <textarea
          rows={2}
          value={prompt}
          disabled={locked}
          aria-label="AI 어시스턴트에게 요청"
          placeholder="열린 자료에 대해 물어보세요"
          onChange={(event) => setPrompt(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) {
              event.preventDefault();
              void ask();
            }
          }}
          className="resize-none rounded-md border border-subtle bg-surface px-3 py-2 text-body outline-none placeholder:text-muted focus-visible:border-accent focus-visible:ring-2 focus-visible:ring-accent/30 disabled:bg-surface-sunken"
        />
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="secondary"
            disabled={locked || !prompt.trim()}
            loading={!!waiting}
            onClick={ask}
          >
            보내기
          </Button>
        </div>
      </div>
    </div>
  );
}
