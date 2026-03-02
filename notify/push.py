"""
推送通知模块。
支持终端打印、Server酱微信推送、钉钉机器人。
"""
from __future__ import annotations

import config
from utils.logger import log


def send(message: str):
    """根据配置的后端推送消息。"""
    backend = config.NOTIFY_BACKEND

    if backend == "console":
        _send_console(message)
    elif backend == "serverchan":
        _send_serverchan(message)
    elif backend == "dingtalk":
        _send_dingtalk(message)
    else:
        _send_console(message)


def _send_console(message: str):
    print(f"[信号] {message}")


def _send_serverchan(message: str):
    """Server酱推送到微信。"""
    key = config.SERVERCHAN_KEY
    if not key:
        log.warning("SERVERCHAN_KEY 未配置")
        _send_console(message)
        return
    try:
        import requests
        url = f"https://sctapi.ftqq.com/{key}.send"
        resp = requests.post(url, data={"title": "交易信号", "desp": message}, timeout=10)
        if resp.status_code == 200:
            log.debug("Server酱推送成功")
        else:
            log.warning(f"Server酱推送失败: {resp.status_code}")
    except Exception as e:
        log.error(f"Server酱推送异常: {e}")
        _send_console(message)


def _send_dingtalk(message: str):
    """钉钉群机器人推送。"""
    webhook = config.DINGTALK_WEBHOOK
    if not webhook:
        log.warning("DINGTALK_WEBHOOK 未配置")
        _send_console(message)
        return
    try:
        import requests
        payload = {"msgtype": "text", "text": {"content": f"[策略信号] {message}"}}
        resp = requests.post(webhook, json=payload, timeout=10)
        if resp.status_code == 200:
            log.debug("钉钉推送成功")
        else:
            log.warning(f"钉钉推送失败: {resp.status_code}")
    except Exception as e:
        log.error(f"钉钉推送异常: {e}")
        _send_console(message)
