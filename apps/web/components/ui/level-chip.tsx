import { cn } from "./cn";

/** BARS 수준(rubric.md). 1~5 밖의 값은 타입에서 막는다 */
export type Level = 1 | 2 | 3 | 4 | 5;

type LevelChipBase = {
  /** 목록용 짧은 표기의 차원·요소 이름("설득 3"). 없으면 "수준 N (1~5)" */
  label?: string;
  /** 담당자가 조정한 값이면 true("담당자 조정됨" 글자 동반) */
  adjusted?: boolean;
  className?: string;
};

/**
 * 수준이 있으면 사유를 받지 않고, 기록 없음(null)이면 사유 문장(rubric.md 5장)이 반드시 따라온다.
 * state는 기록 상태 이름(장면 없음·반응 없음·판단 보류, rubric.md 2.3). 없으면 "기록 없음"
 */
export type LevelChipProps = LevelChipBase &
  (
    | { level: Level; reason?: never; state?: never }
    | { level: null; reason: string; state?: string }
  );

/**
 * BARS 수준 표기. 높고 낮음을 색으로 서열화하지 않도록 수준과 상관없이 중립 톤 하나만 쓴다.
 */
export function LevelChip(props: LevelChipProps) {
  const { level, label, adjusted, className } = props;
  if (props.level === null) {
    return (
      <span className={cn("inline-flex items-center gap-1.5 text-sm text-muted", className)}>
        <span className="rounded-sm border border-line bg-surface px-1.5 py-px text-xs font-medium">
          {label ? `${label} · ${props.state ?? "기록 없음"}` : (props.state ?? "기록 없음")}
        </span>
        <span>{props.reason}</span>
        {adjusted && <AdjustedMark />}
      </span>
    );
  }
  return (
    <span className={cn("inline-flex items-center gap-1.5", className)}>
      <span className="inline-flex items-center rounded-sm border border-line bg-surface px-1.5 py-px text-xs font-medium whitespace-nowrap text-ink tabular-nums">
        {label ? `${label} ${level}` : `수준 ${level} (1~5)`}
      </span>
      {adjusted && <AdjustedMark />}
    </span>
  );
}

function AdjustedMark() {
  return <span className="text-xs text-muted">담당자 조정됨</span>;
}
