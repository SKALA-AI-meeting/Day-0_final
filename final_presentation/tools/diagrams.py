"""덱용 다이어그램 SVG 4장. 1172x400 viewBox, 표시 배율 1.0 기준 최소 글자 16px."""
import sys
from pathlib import Path

OUT = Path(sys.argv[1])
INK, MUTED, LINE, CARD, PAPER = "#141413", "#6B6A64", "#DEDCD3", "#F0EEE6", "#FAF9F5"
ACC, ACC_D, ACC_S = "#D97757", "#B5532F", "#F5E3DA"
FONT = "Pretendard, 'Apple SD Gothic Neo', 'Noto Sans KR', sans-serif"
W, H = 1172, 400


def svg(body):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {W} {H}" width="{W}" height="{H}" '
            f'font-family="{FONT}">\n<defs>'
            f'<marker id="a" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{MUTED}"/></marker>'
            f'<marker id="aa" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="8" markerHeight="8" orient="auto-start-reverse">'
            f'<path d="M0,0 L10,5 L0,10 z" fill="{ACC_D}"/></marker></defs>\n{body}</svg>\n')


def box(x, y, w, h, title, sub=None, kind="plain", tag=None, sub2=None):
    fill, stroke, sw, dash = PAPER, LINE, 1.5, ""
    if kind == "card":
        fill = CARD
    elif kind == "accent":
        fill, stroke, sw = ACC_S, ACC, 2.5
    elif kind == "ink":
        fill, stroke = INK, INK
    elif kind == "dash":
        dash = ' stroke-dasharray="6 5"'
    tc = PAPER if kind == "ink" else INK
    sc = LINE if kind == "ink" else MUTED
    lines = [l for l in (sub, sub2) if l]
    total = 24 + 24 * len(lines)
    ty = y + h / 2 - total / 2 + 19
    s = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="6" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{dash}/>\n'
    s += f'<text x="{x + w / 2}" y="{ty}" text-anchor="middle" font-size="20" font-weight="800" fill="{tc}">{title}</text>\n'
    for i, l in enumerate(lines):
        s += f'<text x="{x + w / 2}" y="{ty + 26 + 24 * i}" text-anchor="middle" font-size="16" fill="{sc}">{l}</text>\n'
    if tag:
        s += (f'<rect x="{x + w - 52}" y="{y - 12}" width="48" height="24" rx="12" fill="{INK if tag == "코드" else ACC}"/>'
              f'<text x="{x + w - 28}" y="{y + 5}" text-anchor="middle" font-size="16" font-weight="800" fill="{PAPER}">{tag}</text>\n')
    return s


def arrow(x1, y1, x2, y2, label=None, lx=None, ly=None, accent=False, dash=False):
    c, m = (ACC_D, "aa") if accent else (MUTED, "a")
    d = ' stroke-dasharray="6 5"' if dash else ""
    s = f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{c}" stroke-width="2"{d} marker-end="url(#{m})"/>\n'
    if label:
        s += f'<text x="{lx if lx is not None else (x1 + x2) / 2}" y="{ly if ly is not None else (y1 + y2) / 2 - 8}" text-anchor="middle" font-size="16" fill="{c}" font-weight="700">{label}</text>\n'
    return s


def text(x, y, t, size=16, color=MUTED, weight=400, anchor="start"):
    return f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" text-anchor="{anchor}">{t}</text>\n'


# 8 · 아키텍처
b = ""
b += box(10, 70, 150, 80, "지원자", "토론방", kind="card")
b += box(10, 250, 150, 80, "담당자", "리포트", kind="card")
b += box(215, 140, 170, 120, "web", "Next.js")
b += arrow(160, 110, 213, 175)
b += arrow(160, 290, 213, 225)
b += f'<rect x="440" y="20" width="300" height="360" rx="6" fill="{ACC_S}" stroke="{ACC}" stroke-width="2.5"/>\n'
b += text(590, 52, "api", 22, INK, 800, "middle") + text(590, 76, "FastAPI + LangGraph", 16, MUTED, 400, "middle")
b += box(460, 96, 260, 82, "토론 그래프", "진행 매니저 + 동료 3명")
b += box(460, 192, 260, 82, "스케줄러", "단계 마감 · 카드 · 침묵 초대")
b += box(460, 288, 260, 76, "인증", "담당자·지원자 토큰 분리")
b += arrow(385, 200, 438, 200, "REST · WS", 412, 188)
b += box(195, 296, 190, 84, "Azure Speech", "받아 적기 · 음성 합성", kind="dash")
b += arrow(438, 338, 387, 338)
b += box(800, 70, 170, 74, "debate-mcp", "토론 상태")
b += box(800, 160, 170, 74, "rag-mcp", "자료 검색")
b += box(800, 250, 170, 74, "assessment-mcp", "관찰 기록")
b += arrow(740, 137, 798, 107) + arrow(740, 197, 798, 197) + arrow(740, 257, 798, 287)
b += text(885, 52, "MCP 3개", 16, ACC_D, 800, "middle")
b += box(1010, 20, 152, 150, "PostgreSQL", "쓰기는 api만", sub2="pgvector 검색", kind="card")
b += box(1010, 230, 152, 150, "리포트 워커", "세션 후 큐로 평가", sub2="재시도 · DLQ", kind="card")
b += arrow(1008, 287, 972, 287)
b += f'<path d="M590,20 V8 H1086 V18" fill="none" stroke="{MUTED}" stroke-width="2" marker-end="url(#a)"/>\n'
b += f'<path d="M590,380 V394 H1086 V382" fill="none" stroke="{MUTED}" stroke-width="2" marker-end="url(#a)"/>\n'
(OUT / "diagram-architecture.svg").write_text(svg(b))

# 9 · 에이전트 두 시점
b = ""
b += text(10, 30, "토론 중 · 지원자 발언마다 (단계 시작·침묵·차례 넘김·카드 시각은 진행 매니저부터)", 18, ACC_D, 800)
xs = [10, 205, 400, 595, 790, 985]
top = [("지원자 발언", "음성 → 받아 적기", None), ("의도 분류", "제안·동의·반대 등", "LLM"),
       ("진행 매니저", "말할 동료 0~2명", "코드"), ("동료 에이전트", "성향대로 발언", "LLM"),
       ("가드 검사", "금지 표현·입장", "코드"), ("게시", "차례대로 음성 재생", None)]
for x, (t, s, tag) in zip(xs, top):
    b += box(x, 56, 177, 110, t, s, kind="accent" if t == "진행 매니저" else ("card" if tag is None else "plain"), tag=tag)
for x in xs[:-1]:
    b += arrow(x + 177, 111, x + 203, 111)
b += text(10, 232, "토론 후 · 세션이 끝나면", 18, ACC_D, 800)
bot = [("세션 종료", "기획안 제출", None), ("큐", "RabbitMQ", "코드"), ("사실 지표", "발언 수·칸 기록", "코드"),
       ("요소별 판단", "수준·근거·확신도", "LLM"), ("담당자 검토", "여기서 멈춤", None), ("확정", "AI 값과 함께 저장", None)]
for x, (t, s, tag) in zip(xs, bot):
    b += box(x, 258, 177, 110, t, s, kind="ink" if t == "담당자 검토" else ("card" if tag is None else "plain"), tag=tag)
for x in xs[:-1]:
    b += arrow(x + 177, 313, x + 203, 313)
(OUT / "diagram-agents.svg").write_text(svg(b))

# 10 · MCP 접근 경계
b = ""
b += box(10, 40, 260, 130, "동료 에이전트 3명", "고집형 · 소극형 · 비판형", kind="card")
b += box(10, 250, 260, 110, "기록·평가 코드", "진행 매니저 규칙 노드", kind="card", sub2="리포트 워커")
b += box(470, 20, 320, 100, "debate-mcp", "토론 상태 조회 · 기록은 코드가")
b += box(470, 140, 320, 100, "rag-mcp", "확정된 키워드 자료만 검색")
b += box(470, 270, 320, 110, "assessment-mcp", "관찰 기록 · 평가 척도", kind="accent")
b += arrow(270, 80, 468, 70, "조회만 허용", 370, 62)
b += arrow(270, 125, 468, 185, "허용", 370, 142)
b += f'<line x1="270" y1="160" x2="440" y2="290" stroke="{ACC_D}" stroke-width="2.5" stroke-dasharray="7 6"/>\n'
b += (f'<circle cx="448" cy="296" r="15" fill="{ACC_D}"/>'
      f'<path d="M441,289 L455,303 M455,289 L441,303" stroke="{PAPER}" stroke-width="3"/>\n')
b += text(330, 262, "차단", 16, ACC_D, 800, "middle")
b += arrow(270, 320, 468, 320, "토큰으로 허용", 370, 308)
b += f'<rect x="850" y="120" width="312" height="180" rx="6" fill="{PAPER}" stroke="{ACC}" stroke-width="2"/>\n'
b += text(1006, 158, "두 겹으로 막는다", 20, INK, 800, "middle")
b += text(872, 200, "① 노드별 도구 허용 목록", 17, INK, 600)
b += text(872, 236, "② assessment 전용 토큰", 17, INK, 600)
b += text(872, 276, "→ 기준을 보고 행동을 맞출 수 없다", 16, ACC_D, 800)
(OUT / "diagram-mcp.svg").write_text(svg(b))

# 11 · 하네스 8요소
b = ""
b += box(10, 20, 270, 100, "Instructions", "채용 건마다 고정된 프롬프트")
b += box(10, 150, 270, 100, "Context", "최근 대화 · 척도는 동료에게 없음")
b += box(10, 280, 270, 100, "Tools", "노드별 허용 목록 · 호출 횟수 제한")
b += f'<rect x="350" y="20" width="440" height="230" rx="6" fill="{ACC_S}" stroke="{ACC}" stroke-width="2.5"/>\n'
b += text(570, 64, "Agent Loop", 24, INK, 800, "middle")
b += text(570, 94, "세션별 메일박스 · 녹음·받아 적기 중 보류 · 비용 상한", 16, MUTED, 400, "middle")
b += box(450, 124, 240, 96, "LLM 호출", "동료 · 진행 매니저 · 의도 분류", kind="plain", sub2="어시스턴트 · 평가")
b += arrow(280, 70, 348, 100) + arrow(280, 200, 348, 160) + arrow(280, 330, 348, 220)
b += box(350, 280, 440, 100, "State · Memory", "토론·음성 상태는 DB가 원천 · 리포트는 체크포인트", kind="card")
b += f'<line x1="570" y1="252" x2="570" y2="278" stroke="{MUTED}" stroke-width="2" marker-start="url(#a)" marker-end="url(#a)"/>\n'
b += box(860, 20, 302, 110, "Guardrails", "금지 표현 · 근거 없는 판단 거부", sub2="사람 승인 전 확정 없음")
b += box(860, 150, 302, 100, "Recovery", "재시도 · 대체 문구 · 재전사 · DLQ")
b += box(860, 280, 302, 100, "Observability", "실행 추적 · 비용 · 프롬프트 버전", kind="card")
b += arrow(790, 90, 858, 75) + arrow(1011, 130, 1011, 148, None)
b += text(1080, 145, "실패 시", 16, MUTED, 700, "start")
b += arrow(790, 330, 858, 330, None, dash=True)
(OUT / "diagram-harness.svg").write_text(svg(b))
print("ok")
