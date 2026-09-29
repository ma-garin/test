# ds/ — yuki-aidd-kit デザインシステムの出荷物（写し）

出所: `ma-garin/yuki-aidd-kit` `02_共通/ひな形/`（tokens.css・ui/components.css・ui/layout.css・components/feedback.js・components/icons.js）。
**ここは編集しない。** 値を変えるときは kit 側を直してから写す（`python scripts/sync_design_system.py <kit のパス>`）。
読み込み順: tokens → components → layout → icons.js → feedback.js。画面側の CSS は `var(--*)` だけを使う（`check_design.py` NG=0）。
