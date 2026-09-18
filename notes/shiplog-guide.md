# Ship Log development notes

以《星际拓荒》(Outer Wilds) 的太阳系为舞台，由浅至深地学习严肃的物理。
理念是**寻找和理解**：从可观察的现象出发，建立最小模型，做数值实验，推到解析结果，看它在哪里失效，再走向前沿。

> 本项目为非官方的粉丝学习项目，不包含任何游戏资源文件，也不涉及 DLC 内容。

## Ship Log · 问题图谱

仿照游戏飞船日志的 "Rumor Mode"，整个项目的知识地图是一张**问题**之间的图：
每张卡片是一个问题（传闻 `rumor` 或 已探索 `explored`），边表示先修 / 深化 / 类比 / "游戏 vs 现实"。

在线版本：<https://telingc.github.io/nomais-gallery/>

### 本地预览

```powershell
pip install -r shiplog/requirements.txt
python shiplog/build.py --serve 8000      # 构建并在 http://localhost:8000/ 预览
python shiplog/build.py --watch           # 修改 YAML 后自动重建
```

### 添加一个问题

1. 在 `shiplog/entries/<地点>.yaml` 里追加一条：

   ```yaml
   - id: gd-some-question          # kebab-case，全局唯一
     title: 一句话标题
     question: 用一两句话把问题问清楚
     location: giants-deep          # 见 config.yaml 的 locations
     domain: [electrodynamics]
     status: rumor                  # 有了结论、缩略图和 notebook 后改成 explored
     leads_to:
       - {to: gd-charged-core, type: prereq}
   ```

2. 运行 `python shiplog/build.py`。脚本会校验、自动排版（旧卡片位置不变），并把布局写回 `shiplog/layout.json`——请把它一起提交。
3. 已探索的条目需要 `figures/<id>.png` 缩略图（通常由同名 `.py` 脚本生成）和 `facts`。

## 目录

```
shiplog/          问题图谱：YAML 条目、构建脚本、单文件网页
figures/          缩略图及其生成脚本
labs/ book/ exercises/   （规划中）numerical notebooks、LaTeX 章节、习题
```

## 部署

推送到 `main` 后 `.github/workflows/pages.yml` 会自动构建并发布到 GitHub Pages。
首次需要在仓库 **Settings → Pages → Source** 选择 **GitHub Actions**。

## 致谢

- 网页交互依赖 [d3](https://d3js.org/)（ISC License），已随仓库附带 `shiplog/web/d3.v7.min.js`。
- Outer Wilds © Mobius Digital / Annapurna Interactive。