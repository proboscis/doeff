# 5. ai kanban が走らせる agora-controllers と doeff の checkout を置き(doeff は公開の repo — https で鍵なし)、実行用のコピーを今の先端で
#    作る。
#    clone した repo には、その repo の決まりのとおり commit の hook を入れる(担当の commit が repo の hook を通る — 入れていなかった
#    2026-10-08 は、担当の commit が hook を通らず doeff-linter の赤 6 件が main に入った。merge の列ではテストも linter も実行しない):
#    agora-controllers は `poe setup` と同じ core.hooksPath、doeff は `make hooks-install` と同じ pre-commit のコピー(Pod に make は要らない)。
stage agora-controllers
fetch_or_clone git@github.com:proboscis/agora-controllers.git main "$KEEP/repos/agora-controllers"
git -C "$KEEP/repos/agora-controllers" config core.hooksPath .githooks
stage doeff
fetch_or_clone https://github.com/proboscis/doeff.git main "$KEEP/repos/doeff"
(cd "$KEEP/repos/doeff" && hooks=$(git rev-parse --git-path hooks) && mkdir -p "$hooks" \
  && cp scripts/git-hooks/pre-commit "$hooks/pre-commit" && chmod +x "$hooks/pre-commit")
