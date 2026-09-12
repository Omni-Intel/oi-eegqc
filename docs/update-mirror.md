# Windows 更新镜像

Windows 客户端默认只访问：

- `https://pack.kunpeng.blog/oi-eegqc/latest.json`
- `https://pack.kunpeng.blog/oi-eegqc/releases/<tag>/OI-EEGQC-Setup-Windows-x64.exe`

green-hk 定时运行 `tools/update_mirror.py`。脚本从固定 GitHub 仓库读取最新 Release，把固定名称的安装器缓存到本机，并重新计算 SHA-256。`latest.json` 只有在安装器完整落盘后才原子替换，因此同步失败不会发布半个安装包。

客户端仍会严格核对固定 HTTPS 主机、版本对应的路径、文件大小和 SHA-256；下载完成及启动安装前各校验一次。`OI_EEGQC_RELEASES_URL` 可在开发环境覆盖元数据入口，正式客户端不需要连接 GitHub API。

Nginx 只需暴露静态目录：

```nginx
location /oi-eegqc/ {
    alias /var/www/oi-eegqc/;
    add_header Cache-Control "public, max-age=300";
}
```

发布同步失败时保留上一份可用版本。同步程序不接触 EEG 数据和上传签名服务。
