# 요소 지침: 수용/유연성 `flexibility`

[초안] 정본은 `docs/product/rubric.md` 3.4·4장. 차원 정의·앵커는 `[요소 척도]` 블록으로 주어진다. 시간 순으로 결정 전(재검토) → 결정 시점(절차 존중) → 결정 후(승복) → 다음 단계(기여)를 본다.

## 보지 않는 것

안의 질·근거의 정확성, 자료 활용 능력, 발언량, 말투의 공손함 그 자체, 처음 자기 안을 강하게 주장했는지(결정 전 주장은 정상 행동), 설득 성공 여부, 기획안 품질, AI 어시스턴트 사용, 상대를 대하는 말의 태도(경청).

## 핵심 장면

- `flexibility_majority`(문제 상황): 강한결(확정 전 불변)과 서지안(설득 가능 규칙: 지원자 반대·대안 2회)이 기준안을 지지한다. 지원자 안이 채택될 수 있다. 창은 카드 시각부터 **해결책 단계 끝**.
- 겹침: `listening_pressure`(문제 상황). 같은 메시지라도 여기서는 자기 입장·결과를 다루는 방식만 본다.
- 보조: 키워드 단계, 해결책 단계(`persuasion_solution` 반론 포함)에서 채택 안 된 키워드·해결책에 대한 반응, 반론 뒤 자기 안 보완·통합, 각 칸 확정 방식. 기획안 단계에서 제출본 `problem` 필드를 칸 결과와 다르게 바꿨는지(칸 결과대로 쓰면 중립).
- 핵심 장면은 `[사실]`이 문제 상황 칸 결정에서 이미 잘라 준다: `flex_openness`는 결정까지(`decision_cut.timing = before`), `flex_acceptance`·`flex_forward`는 결정 뒤만(`after`), `flex_decision_respect`는 카드 창 전체. 키워드·해결책에서 채택 안 된 경우 같은 보조 장면은 그 차원 `scenes`의 보조 장면 id를 단다. 결정 시각·경로·확정 시점 지지 현황과 지원자 안 채택 여부는 `[사실]` `decisions`의 `problem` 행을 쓴다.

## 특수 지침

- **지원자 안 채택이면 요소 전체가 기록 없음(R1)**: 문제 상황 칸의 `option_id`가 지원자가 지지한 안(기준안이 아닌 안)이고, 그 확정이 AI 동료 합의 채움·시간 초과이거나 확정 시점 스냅샷에서 지원자 포함 지지 3명 이상인 지원자 확정이면(`[사실]` `decisions`의 `problem` 행 `candidate_option_adopted = true`), 네 차원 모두 `not_applicable`(`rationale`: "지원자 안 채택으로 설계된 장면이 성립하지 않음"). 반대가 남은 상태의 단독 확정(스냅샷상 지원자 안 지지 3명 미만)은 채택이 아니며 아래 규칙으로 판단한다.
- **확정 행위는 `flex_decision_respect`만 판단한다.** 반대가 남았는데 혼자 칸을 확정한 행위 자체는 이 차원이 본다. `flex_acceptance`는 확정 행위 자체로 낮게 판단하지 않는다.
- **지원자가 혼자 자기 안으로 확정한 경우**(문제 상황 칸 `filled_by = candidate`, `option_id` = 지원자 안, 스냅샷상 반대가 남음):
  - `flex_decision_respect`: 직전에 반대 확인·동의 요청이 없으면 1, 형식적 확인 뒤 반대 무시면 2. 근거 = 칸 확정 기록 + 직전 지원자 메시지(반대가 남았음을 보인 AI 동료 발언은 `rationale`에 적는다). 메시지 근거가 하나도 없으면 확정 기록만으로 1을 주되 확신도 `low`.
  - `flex_acceptance`: **결정 후 행동만** 본다. 결정 후 지원자 발언이 없으면 `no_activity`. 발언이 있으면 그것으로 판단하되, 채택되지 않은 자기 안이 없으므로 보조 장면(키워드·해결책에서 채택 안 된 경우)이 없으면 `not_applicable`.
  - `flex_openness`: 확정 전 메시지로 따로 판단한다.
  - `flex_forward`: 핵심 장면의 "채택되지 않은 뒤"가 없으므로 보조 장면으로 판단하고, 없으면 `not_applicable`.
  - 확정 직후 "잘못 눌렀어요" 같은 오클릭 정황이 있으면 수준을 정하되 확신도 `low`와 사유를 남긴다.
- **결정 후 무발언은 기록 없음**이다(`flex_acceptance` `no_activity`). 승복 실패로 보지 않는다.
- **근거 있는 이의 제기는 감점이 아니다(R1)**: 결정 전 다수안의 근거를 짚고 근거를 들어 반대하는 것은 `flex_openness` 4 이상이 될 수 있다. 결정 후 "의견은 남기되 따를게요"는 `flex_acceptance` 긍정 신호다.
- 시간에 따라 바뀌면 나중 행동에 무게를 둔다. 처음 아쉬움을 보였다가 곧 승복·기여했으면 승복 3~4가 가능하다. 승복 뒤 재론·이탈이 나오면 `flex_acceptance`는 2 이하로 제한한다.
- **설득으로 지지를 바꾼 경우**(서지안의 지지 이동, 세 단계 모두 가능): 정당한 설득은 감점하지 않는다. 새 근거로 상대 근거에 응답하고, 상대가 지지를 바꾼다고 명시한 뒤 확정하고, 확정 전 남은 반대를 확인했으면 중립~긍정이다. 같은 주장 반복, 침묵·모호한 반응을 동의로 간주한 확정, 시간 압박 결론은 부정이다. 설득 성공은 `flex_openness` 상한을 낮추지 않는다. 서지안의 이동은 규칙 결과로 읽고 스냅샷이 그와 다르면 분류 오류일 수 있으니 원문으로 확인한다.
- 처음부터 다수안에 동의해 자기 안이 없으면 `flex_acceptance` `not_applicable`. 즉시 동조는 감점하지 않되 `flex_openness` 상한은 3.
- 시간 초과로 결정됐으면 지원자가 확정하지 않은 것 자체는 감점이 아니다. 시간 초과 직후 반응으로 승복을 본다.
- 후보 밖 안(`cand_<n>`)도 자기 안으로 똑같이 다룬다.
- "좋아요" 뒤에 재론하면 공손한 표현이 아니라 재론으로 판단한다.

## 경계

- 반대가 남은 단독 확정·결론 강행: 이 요소의 `flex_decision_respect`가 주 담당이다. 경청·배려·정직·설득 어느 차원도 확정 행위 자체로 낮추지 않는다.
- 결정 결과 승복·재론·이탈: 주 담당 `flex_acceptance`, `flex_forward`는 다음 단계에서 결정을 전제로 기여했는지.
- 결정 전 다른 안 검토 거부는 `flex_openness`. 문제 상황 다수안 장면의 근거 없는 즉시 동조는 `flex_openness`(상한 3).
- 기획안 `problem` 필드를 칸 결과와 다르게 바꿈: `flex_acceptance` 보조 신호.
- 확정 직전 합의 상태 진술: 사실 여부는 `hon_process_truth`, 동의를 실제로 확인했는지(절차)는 `flex_decision_respect`.

## 사용하는 사실 지표

`own_option_rejected`(`flex_acceptance`·`flex_forward` 적용 여부), `own_options_proposed`/`own_options_adopted`(보조 장면 채택 여부), `slot_records`(문제 상황 칸 `resolution`·`filled_by`·`option_id`·확정 시각·지지 현황 스냅샷, 단독 확정 판단), `messages_after_rejection`(0이면 `flex_acceptance` `no_activity`), `message_count`(해결책 단계 0이면 `flex_forward` `no_activity`), `intent_counts`(결정 후 `oppose` 지속 등 재론 후보, 원문 확인 필수), `spontaneous_count`/`invited_count`·`silence_invites_received`(표시만), `first_message_sec`(참고, 단독 근거 금지), `connection_gaps`, `observations`. 지표 밖 입력: 제출 기획안 `problem` 필드(보조 신호).
