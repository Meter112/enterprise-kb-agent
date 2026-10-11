"""容器内 torch 2.6.0 兼容补丁（sitecustomize：解释器启动即自动执行，先于一切业务 import）。

背景：pytorch CPU index 的 cp313 最高只有 torch 2.6.0（2.7+ 未发布 cp313 CPU 轮子），
而锁定的 sentence-transformers 6.x 强制 transformers>=5，transformers 5.x import 时调用
torch._C._accelerator_getAccelerator()——torch 2.6.0 在无加速器环境下抛 RuntimeError
（2.7 修复为返回 None 的已知 bug），导致容器启动即崩。
patch 后 transformers 走纯 CPU 路径，与容器 CPU 推理一致。仅 2.6.x 触发，不影响 host
（Windows 上 torch 为锁定的 2.14.1）。
"""
import torch

if torch.__version__.startswith("2.6."):
    torch._C._accelerator_getAccelerator = lambda: None
