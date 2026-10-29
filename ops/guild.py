# ops/guild.py
"""多角色公会（二期）：一个用户并行创建多个 NPC，各自独立的时间总线。

- 每个角色一份 UserStore + Runner + 状态（data/<char_id>/），完全隔离；
- 登录瞬间 catch_up_all()：线程并行追平所有角色 → 各自回放离线期主动消息；
- poll_all()：挂机轮询（60s），角色在用户聊别的角色时也会"发消息"；
- LLM 客户端可共享一套（llm_client 自带密钥池轮询锁，线程安全）。
"""
import json
import os
import threading
import uuid

from ops.participants import Participant, make_human_participant, make_npc_profile
from ops.product_default_direct_provision import (
    ProductDefaultDirectProvisionRegistry, create_default_direct_provision,
)
from product_adapters.cx_six_seven_v1.experiment_interface import (
    ProductExperimentInterface,
)
from ops.product_runtime_session import ProductRuntimeSessionPlan
from ops.runner import Runner
from ops.legacy_runner_ingress import submit_compat_runner_message
from ops.runtime_package_composition import (
    RuntimePackageComposition,
    RuntimeSessionInitialization,
)
from ops.storage import UserStore


LIVE_SESSION_CLASS = "live"


def is_live_profile(profile):
    """产品会话资格：缺少标记的旧档案默认只作兼容/维护读取。

    这不是删除或迁移旧数据的判断。它只让产品入口能明确区分新建的可交互会话与
    早期测试/手工档案；维护工具和兼容 Guild 可显式保留 ``include_legacy=True``。
    """
    return isinstance(profile, dict) and profile.get("session_class") == LIVE_SESSION_CLASS


def is_visible_profile(profile):
    """返回角色是否仍应出现在产品会话列表中。

    ``ui_hidden`` 是可逆的产品入口标记，而不是数据生命周期操作：它不会删除
    profile、消息、记忆、trace 或事件流。把这个判断放在 Guild 边界，避免 UI
    和登录页各自维护一份“好友是否存在”的规则。
    """
    return isinstance(profile, dict) and not bool(profile.get("ui_hidden"))


def is_visible_live_profile(profile):
    """产品入口可继续交互的会话资格。"""
    return is_live_profile(profile) and is_visible_profile(profile)


class Guild:
    def __init__(self, base_dir="data", agents=None, runner_kwargs=None,
                 include_legacy=True, *, human_participant_id="human:local",
                 participant_nonce_factory=None,
                 formal_runtime_plan_factory=None,
                 formal_required_reply_host_factory=None):
        self.base_dir = base_dir
        self.agents = agents or {}
        self.runner_kwargs = runner_kwargs or {}
        self.include_legacy = bool(include_legacy)
        # 当前桌面端还没有独立账户 UI；这个显式配置是本地已登录用户的稳定 identity，
        # 不是由 char_id / 显示名 / 旧档案推断出来的 Participant。调用方可在接入真实
        # 登录层后传入账户提供的 ID，而无需改变创建角色 UI。
        self.human_participant_id = str(human_participant_id or "").strip()
        if not self.human_participant_id:
            raise ValueError("human_participant_id 不能为空")
        self._participant_nonce_factory = participant_nonce_factory or (lambda: uuid.uuid4().hex)
        if not callable(self._participant_nonce_factory):
            raise TypeError("participant_nonce_factory 必须可调用")
        if (formal_runtime_plan_factory is not None
                and not callable(formal_runtime_plan_factory)):
            raise TypeError("formal_runtime_plan_factory 必须可调用或为 None")
        self._formal_runtime_plan_factory = formal_runtime_plan_factory
        if (formal_required_reply_host_factory is not None
                and not callable(formal_required_reply_host_factory)):
            raise TypeError(
                "formal_required_reply_host_factory 必须可调用或为 None")
        self._formal_required_reply_host_factory = (
            formal_required_reply_host_factory)
        self.default_direct_provisions = ProductDefaultDirectProvisionRegistry(
            base_dir=self.base_dir)
        self.runners = {}
        self._formal_required_reply_hosts = {}
        self.char_ids = []

    # ------------------------------------------------------------------
    def _make_runner(self, char_id):
        store = UserStore(char_id, base_dir=self.base_dir)
        runner = Runner(store, **self.agents, **self.runner_kwargs)
        identity = (runner.profile or {}).get("product_participant_identity")
        if isinstance(identity, dict):
            runner.bind_output_identity(identity)
        return runner

    def _provision_default_direct_conversation(self, char_id, runner):
        """为刚创建的 NPC 落一条产品 identity 锚，不启动任何互动运行。

        ``char_id`` 只定位已有 local Store；新的 NPC Participant ID 使用本次创建时显式
        mint 的 nonce 并同步保存到 profile。重开时只读取该锚，绝不从 char_id 倒推。
        """
        profile = dict(runner.store.load_profile() or runner.profile or {})
        identity = profile.get("product_participant_identity")
        if isinstance(identity, dict):
            try:
                npc = Participant.from_record(identity)
            except Exception as exc:  # noqa: BLE001 -- 破损 identity 不能被静默重造。
                raise ValueError("产品 NPC participant identity 不可解析") from exc
        else:
            nonce = str(self._participant_nonce_factory() or "").strip()
            if not nonce:
                raise ValueError("participant_nonce_factory 产生了空 identity")
            npc = make_npc_profile(
                participant_id="npc:" + nonce,
                profile_id="npc-profile:" + nonce,
                # 这是对该 NPC 自己已存在 C_base 版本锚的引用；不是用 char_id
                # 冒充 participant identity，也不把 constants 正文复制进 session。
                c_base_ref="store-c-base:%s:%s" % (
                    str(char_id), str(profile.get("session_b0_version") or "initial")),
            ).participant
        human = make_human_participant(self.human_participant_id)
        conversation_id = profile.get("product_direct_conversation_id")
        if not conversation_id:
            conversation_id = "direct:%s:%s" % (human.participant_id, npc.participant_id)
        provision = create_default_direct_provision(
            human=human, npc=npc, conversation_id=conversation_id)
        provision = self.default_direct_provisions.register(provision)
        # profile 仅保存可精确重开 immutable provision 的 locator 与本 NPC identity。
        # 它不保存 B₀ 正文、文本、成员推断或任何 Actor/tick 状态。
        profile["product_participant_identity"] = npc.as_record()
        profile["product_direct_conversation_id"] = provision.conversation_id
        profile["product_human_participant_id"] = human.participant_id
        runner.store.save_profile(profile)
        runner.profile = profile
        runner.bind_output_identity(npc)
        return provision

    def _default_formal_runtime_plan(self, runner, provision):
        """Build one caller-selected create/restore plan from committed facts.

        The factory receives only already committed product facts.  Guild does
        not select packages, mint session IDs, interpret model fields, or read
        the host clock.  Absence of a factory preserves the explicit generic
        Guild seam used by fixtures and third-party products.
        """
        factory = self._formal_runtime_plan_factory
        if factory is None:
            return None
        profile = runner.store.load_profile() or runner.profile or {}
        plan = factory(profile=dict(profile), provision=provision)
        if not isinstance(plan, ProductRuntimeSessionPlan):
            raise TypeError(
                "formal_runtime_plan_factory must return ProductRuntimeSessionPlan")
        return plan

    def _attach_formal_required_reply_host(
        self, char_id, *, runner, provision, plan,
    ):
        factory = self._formal_required_reply_host_factory
        if factory is None:
            return None
        host = factory(runner=runner, provision=provision, plan=plan)
        submit = getattr(host, "submit", None)
        if not callable(submit):
            raise TypeError(
                "formal_required_reply_host_factory 必须返回带 submit 的 host")
        self._formal_required_reply_hosts[char_id] = host
        return host

    def load_default_direct_provision(self, char_id):
        """只读取得新产品会话的 identity anchor；legacy 条目返回 None。"""
        runner = self.runners.get(char_id)
        if runner is None:
            return None
        profile = runner.store.load_profile() or runner.profile or {}
        conversation_id = str(profile.get("product_direct_conversation_id") or "").strip()
        if not conversation_id:
            return None
        provision = self.default_direct_provisions.load(conversation_id)
        if provision is None:
            return None
        if (provision.npc.as_record() != profile.get("product_participant_identity")
                or provision.human.participant_id != profile.get("product_human_participant_id")
                or provision.human.participant_id != self.human_participant_id):
            raise ValueError("产品 direct provision 与 profile identity 锚不一致")
        return provision

    def discover(self):
        """扫描 base_dir 下所有带 profile.json 的角色目录 → 重建 runner。"""
        if not os.path.isdir(self.base_dir):
            return self.char_ids
        ids = []
        for name in sorted(os.listdir(self.base_dir)):
            profile_path = os.path.join(self.base_dir, name, "profile.json")
            if not os.path.isfile(profile_path):
                continue
            try:
                with open(profile_path, "r", encoding="utf-8-sig") as handle:
                    profile = json.load(handle)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            # “删除好友”只隐藏入口，绝不能让它在下一次 discover 时重新出现。
            if not is_visible_profile(profile):
                continue
            if not self.include_legacy and not is_live_profile(profile):
                continue
            ids.append(name)
        for cid in ids:
            if cid not in self.runners:
                runner = self._make_runner(cid)
                if (self._formal_runtime_plan_factory is not None
                        and "runtime_session_binding" in (runner.profile or {})):
                    provision = self.load_default_direct_provision_for_runner(
                        cid, runner)
                    plan = self._default_formal_runtime_plan(runner, provision)
                    self.runners[cid] = runner
                    try:
                        self.activate_formal_runtime_session(
                            cid, plan.composition,
                            initialization=plan.initialization,
                        )
                        self._attach_formal_required_reply_host(
                            cid, runner=runner, provision=provision, plan=plan)
                    except Exception:
                        self.runners.pop(cid, None)
                        self._formal_required_reply_hosts.pop(cid, None)
                        raise
                else:
                    self.runners[cid] = runner
        # runner 缓存不定义产品可见性；只让本次发现到的会话进入调度入口。
        self.char_ids = sorted(ids)
        return self.char_ids

    def load_default_direct_provision_for_runner(self, char_id, runner):
        """Validate an unpublished Runner's committed direct provision."""
        profile = runner.store.load_profile() or runner.profile or {}
        conversation_id = str(
            profile.get("product_direct_conversation_id") or "").strip()
        if not conversation_id:
            raise ValueError(
                "formal product session requires a committed direct provision")
        provision = self.default_direct_provisions.load(conversation_id)
        if provision is None:
            raise ValueError("产品 direct provision 不存在")
        if (provision.npc.as_record()
                != profile.get("product_participant_identity")
                or provision.human.participant_id
                != profile.get("product_human_participant_id")
                or provision.human.participant_id != self.human_participant_id):
            raise ValueError("产品 direct provision 与 profile identity 锚不一致")
        return provision

    def new_character(self, char_id, card, preference_text=None,
                      preference_declared=None, initial_state=None):
        """角色卡开局新角色并注册。返回 runner。"""
        runner = self._make_runner(char_id)
        prepared_card = dict(card or {})
        if initial_state is not None:
            prepared_card["initial_state"] = initial_state
        runner.setup_from_card(prepared_card, background_text="角色卡：" + char_id,
                               preference_text=preference_text or None,
                               preference_declared=preference_declared)
        profile = runner.store.load_profile() or {}
        profile["session_class"] = LIVE_SESSION_CLASS
        runner.store.save_profile(profile)
        runner.profile = profile
        provision = self._provision_default_direct_conversation(char_id, runner)
        plan = self._default_formal_runtime_plan(runner, provision)
        self.register(char_id, runner)
        if plan is not None:
            try:
                self.activate_formal_runtime_session(
                    char_id, plan.composition,
                    initialization=plan.initialization,
                )
                self._attach_formal_required_reply_host(
                    char_id, runner=runner, provision=provision, plan=plan)
            except Exception:
                self.unregister(char_id)
                raise
        return runner

    def new_character_from_background(self, char_id, background_text, preference_text=None,
                                      preference_declared=None, role_weights_override=None,
                                      initial_state=None):
        """背景解析开局的产品入口；与模板开局同样先形成可恢复 live 会话。

        背景解析只发生在用户明确填写背景时。``role_weights_override`` 是创建器中
        显式确认的角色五维，覆盖 Agent 的初始建议但不触及用户 B₀。
        """
        if not str(background_text or "").strip():
            raise ValueError("角色背景不能为空")
        runner = self._make_runner(char_id)
        runner.setup(str(background_text).strip(), preference_text=preference_text or None,
                     preference_declared=preference_declared,
                     role_weights_override=role_weights_override,
                     initial_state=initial_state)
        profile = runner.store.load_profile() or {}
        profile["session_class"] = LIVE_SESSION_CLASS
        runner.store.save_profile(profile)
        runner.profile = profile
        provision = self._provision_default_direct_conversation(char_id, runner)
        plan = self._default_formal_runtime_plan(runner, provision)
        self.register(char_id, runner)
        if plan is not None:
            try:
                self.activate_formal_runtime_session(
                    char_id, plan.composition,
                    initialization=plan.initialization,
                )
                self._attach_formal_required_reply_host(
                    char_id, runner=runner, provision=provision, plan=plan)
            except Exception:
                self.unregister(char_id)
                raise
        return runner

    def register(self, char_id, runner):
        self.runners[char_id] = runner
        self.char_ids = sorted(self.runners.keys())
        return runner

    def attach_formal_runtime_composition(self, char_id, composition):
        """Attach one externally selected package pair to a registered Runner.

        This product seam owns no package construction, session-ID minting, or
        initialization defaults.  New sessions still require an explicit call
        to ``Runner.bootstrap_formal_runtime_session`` after attachment.
        """
        if not isinstance(composition, RuntimePackageComposition):
            raise TypeError("composition must be RuntimePackageComposition")
        runner = self.runners.get(char_id)
        if runner is None:
            raise KeyError("formal runtime requires a registered character")
        return runner.attach_formal_runtimes(
            model_runtime=composition.model_runtime,
            temporal_runtime=composition.temporal_runtime,
            runtime_variant=composition.variant_binding,
        )

    def activate_formal_runtime_session(
        self, char_id, composition, *, initialization=None,
    ):
        """Create a new formal session or strictly restore an existing one."""
        if not isinstance(composition, RuntimePackageComposition):
            raise TypeError("composition must be RuntimePackageComposition")
        runner = self.runners.get(char_id)
        if runner is None:
            raise KeyError("formal runtime requires a registered character")
        profile = runner.profile
        has_anchor = (
            isinstance(profile, dict)
            and "runtime_session_binding" in profile
        )
        if has_anchor:
            if initialization is not None:
                raise ValueError(
                    "existing formal session restore rejects initialization")
            return runner.attach_formal_runtimes(
                model_runtime=composition.model_runtime,
                temporal_runtime=composition.temporal_runtime,
                runtime_variant=composition.variant_binding,
            )
        if not isinstance(initialization, RuntimeSessionInitialization):
            raise TypeError(
                "new formal session requires RuntimeSessionInitialization")
        return runner.activate_formal_runtimes(
            model_runtime=composition.model_runtime,
            temporal_runtime=composition.temporal_runtime,
            runtime_variant=composition.variant_binding,
            initial_coordinate=initialization.initial_coordinate,
            parameter_payload=initialization.parameter_payload,
            state_payload=initialization.state_payload,
        )

    def unregister(self, char_id):
        self.runners.pop(char_id, None)
        self._formal_required_reply_hosts.pop(char_id, None)
        self.char_ids = sorted(self.runners.keys())

    def formal_required_reply_handler(self, char_id):
        """Return the injected W2 transport callable for one formal owner."""
        host = self._formal_required_reply_hosts.get(char_id)
        if host is None:
            return None

        def handle(text, *, now_ms):
            return host.submit(text, now_ms=int(now_ms)).ui_envelope()

        return handle

    def formal_experiment_handler(self, char_id):
        """Return the receipt-first public experiment callable for one owner.

        This is a projection over the same formal host used by the product UI;
        it does not create a second execution or provider path.
        """
        host = self._formal_required_reply_hosts.get(char_id)
        if host is None:
            return None
        interface = ProductExperimentInterface(host.submit)
        return interface.submit_external_event

    def attach_formal_recipient_actor(self, char_id, actor):
        """Register one real owner Actor at the injected product composition."""
        runner = self.runners.get(char_id)
        if runner is None:
            raise KeyError("formal recipient requires a registered character")
        binder = getattr(
            self._formal_required_reply_host_factory,
            "bind_formal_recipient_actor",
            None,
        )
        if not callable(binder):
            return None
        participant = runner.formal_owner_participant_identity()
        return binder(participant.participant_id, actor)

    def detach_formal_recipient_actor(self, char_id, actor):
        """Remove only the exact owner Actor that is being stopped."""
        runner = self.runners.get(char_id)
        if runner is None:
            return False
        unbinder = getattr(
            self._formal_required_reply_host_factory,
            "unbind_formal_recipient_actor",
            None,
        )
        if not callable(unbinder):
            return False
        participant = runner.formal_owner_participant_identity()
        return unbinder(participant.participant_id, actor)

    # ------------------------------------------------------------------
    def _map(self, fn):
        """并行跑 fn(runner) → {char_id: result}（线程池，结果按序收集）。"""
        results = {}
        threads = []
        lock = threading.Lock()
        for cid, runner in list(self.runners.items()):
            def work(cid=cid, runner=runner):
                try:
                    res = fn(runner)
                except Exception as e:  # noqa: BLE001 —— 单角色失败不拖垮公会
                    res = {"sent": False, "messages": [], "error": str(e)}
                with lock:
                    results[cid] = res
            th = threading.Thread(target=work, daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        return results

    def catch_up_all(self, now_ms=None, on_message=None, parallel=False):
        """登录衔接：并行追平所有角色，回放离线期主动消息。

        on_message(cid, msg)：每条回放消息生成后立即回调（工作线程内触发，
        GUI 用它做"登录后一条条冒出来"的逐条揭示）。
        parallel：每个角色内部也并行生成（开口点相隔以小时计，独立时刻）。
        """
        results = {}
        threads = []
        lock = threading.Lock()
        for cid, runner in list(self.runners.items()):
            def work(cid=cid, runner=runner):
                try:
                    if runner.formal_runtime_session is not None:
                        res = {
                            "sent": False,
                            "messages": [],
                            "reason": "formal_runtime_pending_w6",
                        }
                    else:
                        res = runner.catch_up(
                            now_ms=now_ms, parallel=parallel,
                            on_message=(lambda m, cid=cid: on_message(cid, m))
                            if on_message else None)
                except Exception as e:  # noqa: BLE001 —— 单角色失败不拖垮公会
                    res = {"sent": False, "messages": [], "error": str(e)}
                with lock:
                    results[cid] = res
            th = threading.Thread(target=work, daemon=True)
            th.start()
            threads.append(th)
        for th in threads:
            th.join()
        return results

    def poll_all(self, now_ms=None):
        """挂机轮询：同 catch_up（增量积分，无新 gap 时不重复生成）。"""
        return self.catch_up_all(now_ms=now_ms)

    def on_message(self, char_id, text, now_ms=None, delay_ms=None):
        runner = self.runners.get(char_id)
        if runner is None:
            raise KeyError(f"未知角色：{char_id}")
        if runner.formal_runtime_session is not None:
            handler = self.formal_required_reply_handler(char_id)
            if handler is None:
                raise RuntimeError(
                    "formal required host is unavailable; legacy direct fallback is forbidden")
            if delay_ms is not None:
                raise ValueError(
                    "formal required path does not accept legacy delay_ms")
            if now_ms is None:
                import time
                now_ms = int(time.time() * 1000)
            return handler(text, now_ms=int(now_ms))
        return submit_compat_runner_message(
            runner, text, now_ms=now_ms, delay_ms=delay_ms)
