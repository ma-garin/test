"""policy bundle の発行・検証（安全施策.md SC-01 / SEC-01の開発用fake実装）。

`issue_capability`（authority.py）は、request.policy_bundle_sha256 が
実際に署名済みの `PolicyBundle` を指しているかをここで検証する。
未登録・無効化済み・署名不一致のbundleはすべて拒否する
（SEC-01: 未署名または別コミットのpolicy bundleを読み込むと拒否される）。
"""

from __future__ import annotations

import hashlib
import hmac
import json

from django.conf import settings

from apps.pmo_authority.models import PolicyBundle
from apps.pmo_authority.services.authority import _signing_key

# authority.sign_payload は capability 専用の固定フィールド集合
# （_SIGNED_FIELDS）にしか対応していないため、形の異なる policy bundle の
# 署名にはそのまま使えない。署名鍵（_signing_key）だけを共有し、
# ここでは bundle 用に独立した署名処理を持つ。


class PolicyBundleRejected(ValueError):
    """未署名・無効化済み・署名不一致のpolicy bundleを拒否したことを表す。"""


class InsecureDevBundleError(RuntimeError):
    """DEBUG=False（本番相当）なのに開発用の既定policy bundleを使おうとしたことを表す。"""


def _canonical_bundle_payload(content_sha256: str, commit_sha: str) -> str:
    return json.dumps(
        {"content_sha256": content_sha256, "commit_sha": commit_sha}, sort_keys=True, ensure_ascii=True
    )


def _sign_bundle(content_sha256: str, commit_sha: str) -> str:
    message = _canonical_bundle_payload(content_sha256, commit_sha)
    return hmac.new(_signing_key(), message.encode("utf-8"), hashlib.sha256).hexdigest()


def publish_bundle(*, content_sha256: str, commit_sha: str) -> PolicyBundle:
    """policy bundle を署名して登録する（本番ではCIが行う操作の開発用代替）。"""

    signature = _sign_bundle(content_sha256, commit_sha)
    return PolicyBundle.objects.create(
        content_sha256=content_sha256, commit_sha=commit_sha, signature=signature
    )


#: D-04未決定の間、Outboxのfake実行が参照する開発用の既定bundle。
DEV_DEFAULT_CONTENT_SHA256 = "dev-fake-policy-bundle-content-hash"
_DEV_DEFAULT_COMMIT_SHA = "dev-fake-commit-sha"


def get_or_create_dev_default_bundle() -> PolicyBundle:
    """開発用の既定bundleを取得する。無ければ自動でpublishする。

    本番ではCIが生成した実際のbundleのみを使うべきで、これは
    fake実装（D-04未決定の間のOutbox）専用の開発用ヘルパー。

    セキュリティレビュー指摘: authority._signing_key と異なり、この関数
    自体には本番相当(DEBUG=False)での使用を止めるガードが無く、
    outbox.pyが気づかず既定bundleを使い続けられる抜け穴だった。
    DEBUG=False かつ settings.PMO_AUTHORITY_ALLOW_DEV_POLICY_BUNDLE が
    明示的にTrueでない限り拒否する。
    """

    if not getattr(settings, "DEBUG", True) and not getattr(
        settings, "PMO_AUTHORITY_ALLOW_DEV_POLICY_BUNDLE", False
    ):
        raise InsecureDevBundleError(
            "DEBUG=False（本番相当）で開発用の既定policy bundleを使おうとしています。"
            "D-04（外部反映の許可）確定後、CIが生成した実際のbundleを"
            "publish_bundle()で登録してください"
            "（安全施策.md 11章: CI attestationの実装者が決まるまでは開発用途に限定）。"
        )

    try:
        return PolicyBundle.objects.get(content_sha256=DEV_DEFAULT_CONTENT_SHA256)
    except PolicyBundle.DoesNotExist:
        return publish_bundle(content_sha256=DEV_DEFAULT_CONTENT_SHA256, commit_sha=_DEV_DEFAULT_COMMIT_SHA)


def verify_bundle(content_sha256: str) -> PolicyBundle:
    """content_sha256に対応する有効なpolicy bundleを返す。無ければ拒否する。"""

    try:
        bundle = PolicyBundle.objects.get(content_sha256=content_sha256)
    except PolicyBundle.DoesNotExist as error:
        raise PolicyBundleRejected(
            f"未登録のpolicy bundleです（content_sha256={content_sha256}）。"
            "署名済みbundleを先にpublish_bundle()で登録してください。"
        ) from error

    if not bundle.is_active:
        raise PolicyBundleRejected("policy bundleが無効化されています。")

    expected_signature = _sign_bundle(bundle.content_sha256, bundle.commit_sha)
    if expected_signature != bundle.signature:
        raise PolicyBundleRejected("policy bundleの署名が一致しません（改ざんの疑い）。")

    return bundle
