# Control SSH 准备手册

目标：在当前 Windows 用户的 VS Code PowerShell 中，为 `47.112.10.137` 建立经过独立指纹核对的严格 SSH 连接。本文不保存密码或私钥。

## 当前自动检查结果

- Control 的 22 端口可达，远端是 Ubuntu OpenSSH。
- 当前 Codex 沙箱使用隔离用户 `CodexSandboxOffline`，无法读取或修改你真实 Windows 用户的 `~/.ssh`。
- 当前沙箱没有可用 `ssh-agent` 身份。
- 严格测试已按预期停在主机密钥未信任阶段；没有接受密钥，没有登录，也没有修改云端。
- 网络连接中观察到的指纹只能作为待比对值，不能替代云厂商控制台或服务器控制台提供的可信指纹。
- 2026-08-20 从当前网络连接观察到的 ED25519 指纹为 `SHA256:bIZ6KhDj7oklC9M0tsQhug66uB4iejB9kBrPopoPitY`。它只用于和可信控制台结果比对；控制台结果一致后才可传给准备脚本。

## 第一步：从可信渠道取得 ED25519 指纹

在云厂商控制台的服务器终端执行：

```bash
ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub -E sha256
```

复制其中以 `SHA256:` 开头的指纹。不要仅凭首次 SSH 弹窗确认。

## 第二步：运行准备脚本

在你自己的 VS Code PowerShell 中执行，而不是在聊天中粘贴密码或私钥：

```powershell
cd D:\tx\mini-drop

.\scripts\prepare-control-ssh.ps1 `
  -ExpectedEd25519Fingerprint 'SHA256:从可信控制台复制的指纹'
```

脚本会：

1. 从网络读取 Control 的 ED25519 公钥；
2. 计算其 SHA-256 指纹；
3. 与可信渠道的指纹逐字符比较；
4. 只有一致时才备份并写入当前用户 `known_hosts`；
5. 用 `StrictHostKeyChecking=yes` 复核；
6. 执行 BatchMode 登录测试。

如果此时提示没有身份，说明主机密钥信任已经完成，但还缺非交互认证。

## 第三步：使用已有私钥

假设已有私钥位于当前用户 `.ssh` 下：

```powershell
.\scripts\prepare-control-ssh.ps1 `
  -ExpectedEd25519Fingerprint 'SHA256:可信指纹' `
  -IdentityFile "$HOME\.ssh\你的私钥文件"
```

需要同时写入别名配置时：

```powershell
.\scripts\prepare-control-ssh.ps1 `
  -ExpectedEd25519Fingerprint 'SHA256:可信指纹' `
  -IdentityFile "$HOME\.ssh\你的私钥文件" `
  -WriteSshConfig
```

脚本会在修改 `known_hosts` 或 `config` 前创建带时间戳的备份。

## 第四步：没有可用密钥时

在本机生成独立密钥，不要把私钥发到聊天或仓库：

```powershell
ssh-keygen -t ed25519 -a 64 -f "$HOME\.ssh\mini-drop-control-ed25519" -C "mini-drop-control-temporary"
Get-Content "$HOME\.ssh\mini-drop-control-ed25519.pub"
```

只把 `.pub` 公钥通过云厂商控制台或已经可信的登录方式加入服务器：

```text
/root/.ssh/authorized_keys
```

然后运行：

```powershell
.\scripts\prepare-control-ssh.ps1 `
  -ExpectedEd25519Fingerprint 'SHA256:可信指纹' `
  -IdentityFile "$HOME\.ssh\mini-drop-control-ed25519" `
  -WriteSshConfig
```

## 最终验收

有别名时：

```powershell
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes mini-drop-control "printf 'control-connection-ok\n'"
```

没有别名时：

```powershell
ssh -o BatchMode=yes -o StrictHostKeyChecking=yes root@47.112.10.137 "printf 'control-connection-ok\n'"
```

预期只输出：

```text
control-connection-ok
```

成功后可以回复：`Control SSH 已准备好`。

## 安全说明

- 不使用 `StrictHostKeyChecking=no`。
- 不使用 `accept-new` 代替独立指纹核对。
- 不把密码、私钥、API Key 写入命令参数、聊天或 Git。
- 不在没有核对指纹时使用 `ssh-keyscan >> known_hosts`。
- 主机指纹不一致时立即停止，并在云厂商控制台确认服务器是否重装或密钥是否更换。
