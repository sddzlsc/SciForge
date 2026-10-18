# 文件上传：给大模型做附件 / 传到 GPU 服务器

## 1. 对话附件（给大模型）

GUI：**对话** 页 → 输入框左下角 **📎 附件** → 选文件（或直接把文件拖进输入框）→ 发送。

- 支持的文本类：`.txt .md .json .jsonl .csv .tsv .yaml .py .tex .bib .log .html .ipynb`
- **PDF**：自动提取前 50 页文本（pymupdf，缺失时 pypdf 兜底）
- 图片：保存路径并告知模型；当前 DeepSeek 文本模型不直接看图（换多模态模型/自行描述）
- 其他格式（docx/xlsx 等）：只保存路径，模型可用工具读取

行为与限制：
- 上传后保存在 `<project>/uploads/`，文件名为 `时间戳-随机-原文件名`（防覆盖/防穿越）
- 单文件上限 **64MB**；文本摘录进上下文最多约 6k 字符/文件、总计约 30k 字符，未展示部分让模型用 `read_file`/`paper_read` 读取
- 文件不会离开本机（除非你主动上传到 GPU 服务器）
- 发送时可以只发附件、不写文字，会自动生成「请阅读附件并总结」的提示

## 1.5 数据集路径（流水线）

对话页/实验页启动 pipeline 时的「数据集路径」支持三种方式：

- **📁 上传**：选择文件或文件夹（`webkitdirectory`），保存到 `<project>/data/<dataset>/`，保留目录结构，返回本地绝对路径并自动填入
- **拖拽**：把文件/文件夹直接拖到数据集输入框，效果同上
- **🗂 本机**：浏览本机目录（只列目录，不读文件内容），选中后自动填入绝对路径；适合 ImageNet 这类不想复制的已有大数据集
- 也可以手动输入绝对路径或远端路径（pipeline 的 data 阶段会据此上传/校验）

限制：单次上传 ≤ 4GB / 20000 个文件；文件上传会复制一份到项目目录，超大已有数据集建议用「本机」选择绝对路径。

## 2. 上传到 GPU 服务器

GUI：**服务器** 页 → **上传文件到 GPU** 面板：

1. 选择目标服务器（需先在「添加 / 编辑服务器」保存好，支持密码或 Key 登录）
2. 可选填「远端目录」（留空 = 服务器配置里的 `workdir`，如 `~/sciforge`）
3. 选择文件 → 点「上传」

链路：浏览器 → 本机 `<project>/uploads/` → `rsync -az --partial`（无 rsync 时 `scp -r`）→ 服务器目标目录。
密码登录会通过 `SSH_ASKPASS` 自动完成（无需 sshpass）；结果区显示远端目录、文件、耗时与命令输出。

## 3. 命令行等价操作

```bash
# 直接 scp / rsync 到服务器（大文件更快）
scp -P 48322 ./model.ckpt root@connect.example.com:~/sciforge/data/
rsync -az --partial -e "ssh -p 48322" ./data/ root@host:~/sciforge/data/

# 用 SciForge 的同步工具
sciforge ssh sync gpu1 ./data ~/sciforge/data
```

## 4. 安全说明

- 上传文件只保存在项目目录，不会自动执行；GPU 上传只做 rsync/scp。
- 服务默认只监听 `127.0.0.1`，局域网/公网无法直接上传。
- 密码/Key 只存在本机 `~/.sciforge/config.toml`（0600）；网页不回传。
- 谨慎上传含隐私/未公开数据的文件；GPU 服务器上的文件权限由远端账户决定。
