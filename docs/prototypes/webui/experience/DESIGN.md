---
name: NovelLens 探索与阅读视觉样稿
description: 已获认可的探索方向与待确认的阅读状态，仅限本目录。
colors:
  blue: "#2747cf"
  deep: "#172b78"
  ink: "#1a2450"
  muted: "#57617f"
  ground: "#eef0fa"
  analysis: "#f2a27e"
  highlight: "#f9d4bb"
  reading-paper: "#ffffff"
  reading-panel: "#f5f6fb"
  reading-ink: "#272d3d"
  reading-highlight: "#fae9db"
  night-blue: "#493c77"
  night-deep: "#33264f"
  night-analysis: "#d8c3ef"
  night-highlight: "#e3daf3"
  night-reading-highlight: "#eee7f7"
typography:
  body:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei", sans-serif'
    fontSize: "14px"
    lineHeight: 1.6
  explore-title:
    fontFamily: '"Noto Serif SC", "Source Han Serif SC", serif'
    fontSize: "clamp(66px,6.7vw,96px)"
    fontWeight: 550
    lineHeight: 1.17
    letterSpacing: "-0.035em"
  reading-title:
    fontFamily: '"Noto Serif SC", "Source Han Serif SC", serif'
    fontSize: "21px"
    fontWeight: 550
    lineHeight: 1.5
  reading-prose:
    fontFamily: '"Noto Serif SC", "Source Han Serif SC", serif'
    fontSize: "19px"
    fontWeight: 400
    lineHeight: 1.95
rounded:
  annotation-toggle: "5px"
spacing:
  reading-inline: "38px"
  paragraph-gap: "16px"
  paragraph-number-gap: "12px"
---

# NovelLens 探索与阅读视觉样稿

## Overview

**Creative North Star: "原文与分析共同成为视觉主体"**

本文描述本目录当前 HTML/CSS/JavaScript。探索视图方向已获用户认可；本轮新增的阅读状态待用户确认。样稿不构成全项目最终视觉系统，不代表生产数据、全书阅读或章节导航已经完成。

## Colors

探索视图使用钴蓝作品区、浅色空间背景与橙色标注。切换《夜班归途》时，作品区域、标注和引用强调切换为紫色系；颜色不表示分析质量。阅读视图改为白色正文、浅灰标注栏和较浅的引用底色。

## Typography

黑体用于界面，宋体用于书名、正文及标注标题。Noto 字体依赖本机安装，未自托管；其他设备可能使用回退字体。探索原文默认 15px，1200px 及以下为 14px，1550px 起为 17px。阅读正文固定 19px，章节标题 30px，标注说明 15px。文字均为 HTML，可选择，不是图片。

## Layout

PC 最小页面宽度为 960px，没有手机布局。探索视图默认左右分区为 31% / 69%，1200px 及以下改为 29% / 71%；左侧大书名与引句，右侧倾斜原文页、标注及引用线，底部作品切换栏高至少 120px。

阅读状态隐藏底部作品栏，应用顶栏缩至 64px，书名收为 70px 高工具栏中的小标题。正文区与标注栏各自滚动，正文滚动容器最大宽 720px；右栏默认 320px，1100px 及以下为 290px。收起右栏后正文区域扩展，文本仍保留宽度上限。正文段落间距及段号间隔见 token。

## Elevation & Depth

探索使用 CSS 透视、纸页旋转和局部鼠标位移；SVG 曲线连接当前标注与可见的引用段落，不表示实体网络。纸页及标注有阴影。阅读状态取消透视、阴影、背景轨道及引用连线，保留原文高亮。动效参数见 `design.json`。

## Shapes

原文纸页、标注和选择器以直边为主；背景环线是装饰图形。阅读标注开关采用小圆角。焦点使用橙褐色轮廓，与普通选中状态区分。

## Components

- 探索：悬停、聚焦或点击标注选择器更新完整引用及说明；阅读：只点击切换标注，避免鼠标经过造成正文跳转。标签当前为文本展示，不是筛选入口。
- 展开阅读显示当前演示章的段落；定位按钮进入阅读并定位引用。返回探索保留所选标注；再次展开可恢复同一作品的阅读位置。探索中换选标注会清除该作品此前阅读位置。
- 收起或展开标注栏按段落及其相对视口位置保持阅读锚点；主动滚轮或按下指针中止过渡期间的位置校正。记忆只存在于当前页面内存。
- 作品切换仅覆盖《雨停之前》《夜班归途》两部既有虚构样例，切换后选择首条标注。元信息中的章数不代表已实现全部章节导航。
- 没有业务数据库连接、标注编辑、保存、外部 AI 调用或分析评分。减少动态效果偏好关闭过渡、视差和平滑滚动。

## Do's and Don'ts

- Do：探索保留视觉表达，阅读保留正文主体和明确引用；保持两种状态各自的构图职责。
- Do：用真实段落、标注与引用范围核对联动，保留 HTML 文字。
- Don't：将本页演示状态描述为生产能力，或将引用曲线解释成额外关系。
- Don't：将新阅读状态记录为已经获得用户视觉验收。
