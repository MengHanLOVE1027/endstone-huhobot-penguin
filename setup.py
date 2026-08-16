import re
import setuptools

with open("README.md", "r", encoding="utf-8") as fh:
    long_description = fh.read()

# 从源码读取版本号（单一来源，避免与 huhobot_penguin_plugin.py 重复）
with open("src/endstone_huhobot_penguin/huhobot_penguin_plugin.py", "r", encoding="utf-8") as f:
    content = f.read()
    match = re.search(r'^PLUGIN_VERSION\s*=\s*"(.+?)"', content, re.M)
    version = match.group(1) if match else "0.0.0"

setuptools.setup(
    name="endstone-huhobot-penguin",
    version=version,
    author="huohua&MengHanLOVE1027",
    license="AGPL-3.0-or-later",
    description="QQ 开放平台官方机器人与 BDS 之间的聊天 / 命令桥接插件（EndStone Python 版）",
    long_description=long_description,
    long_description_content_type="text/markdown",
    package_dir={"": "src"},
    packages=setuptools.find_packages(where="src"),
)
