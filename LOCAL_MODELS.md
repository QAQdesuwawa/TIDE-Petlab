# 默认本地记忆检索

完整Windows x64 CPU下载包含Qwen3-Embedding-0.6B-Q8_0与Qwen3-Reranker-0.6B-Q8_0，两份原始权重合计1,278,303,776字节，以及llama.cpp b10981 CPU运行时。无需CUDA、PyTorch、Ollama或embedding API。聊天/量测仍使用用户配置的在线API。

首次START：完整包复用local_models；源码版自动从固定官方URL下载到同一目录并验SHA。手动离线验证：`.venv/Scripts/python.exe -B -m petlab_app.model_resources --verify`。不要把个人`*.rag.sqlite3`向量索引、配置、回执或记忆放入仓库。

只对已收到的用户话语做内容寻址，候选来自当前角色、同一会话和该输入之前已经提交、对角色可见的记录。一个索引文档可以包含父输入与有真实回复授权的已提交回答；给Writer时仍分清各条原话的说话者。默认CPU模型在后台预热，模型完成不触发自主发送。动态Tick和状态召回保持原职责。

旧记忆的向量按来源指纹只缓存一次；检索与语义量测并行，额外等待最多500ms。粗到细按计算时间预算检查候选，未检查的候选明确记为未覆盖，不能当作不相关。三个CPU槽只是推理微批量，不是固定召回条数。资源缺失/繁忙/超时/失败分别记录，不改旧历史或放大原数值公式。

精确版本、SHA和资格策略来源见`model_packages/desktop_pet_v1/resources/local-models/manifest.json`。Embedding与Reranker均Apache-2.0，llama.cpp为MIT，附LLVM OpenMP许可。模型未修改。完整包的local_models/MODEL_MANIFEST.json单独覆盖大文件；源码RELEASE_MANIFEST不包含权重。

来源：
- https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF
- https://huggingface.co/ggml-org/Qwen3-Reranker-0.6B-Q8_0-GGUF
- https://github.com/ggml-org/llama.cpp/tree/77d554b26
