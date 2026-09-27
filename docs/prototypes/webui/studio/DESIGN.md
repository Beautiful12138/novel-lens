---
name: NovelLens Studio 作品库视觉提案
description: 现代编辑工作室方向的独立 PC 单页草案，待用户视觉验收。
colors:
  canvas: "#ffffff"
  sidebar: "#f6f7f9"
  ink: "#1d2028"
  muted: "#626875"
  line: "#e6e8ee"
  blue: "#3756d7"
  blue-soft: "#e9edff"
  blue-hover: "#2844b9"
  ready: "#345f4c"
  preparing: "#745d35"
typography:
  display:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei", sans-serif'
    fontSize: "46px"
    fontWeight: 600
    lineHeight: 1.25
    letterSpacing: "-1.5px"
  title:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei", sans-serif'
    fontSize: "25px"
    fontWeight: 600
    letterSpacing: "-0.5px"
  body:
    fontFamily: '"Noto Sans SC", "Microsoft YaHei", sans-serif'
    fontSize: "14px"
    lineHeight: 1.6
  excerpt:
    fontFamily: '"Noto Serif SC", "Source Han Serif SC", serif'
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.95
rounded:
  button: "6px"
  navigation: "7px"
  preview: "10px"
spacing:
  column-gap: "34px"
  content-inline: "58px"
  book-info-top: "24px"
components:
  button-primary:
    backgroundColor: "{colors.blue}"
    textColor: "{colors.canvas}"
    rounded: "{rounded.button}"
    padding: "10px 16px"
  button-primary-hover:
    backgroundColor: "{colors.blue-hover}"
  navigation-current:
    backgroundColor: "{colors.blue-soft}"
    textColor: "{colors.blue}"
    rounded: "{rounded.navigation}"
    padding: "12px 15px"
---

# NovelLens Studio 作品库视觉提案

## Overview

**Creative North Star: "现代编辑工作室"**

本文件仅记录 `studio/` 中 HTML、CSS、JavaScript 的当前视觉草案。用户已确认尝试该方向，整页视觉仍待验收；它不是全项目正式设计系统，不修改产品基线，也不代表生产前端或其他页面已完成。

## Colors

白色内容区、浅灰侧栏与深色文字形成主要层级。钴蓝用于品牌、当前导航、筛选下划线与主操作；准备状态另配文字及图形提示。封面拥有独立局部配色，不承担业务状态含义。

## Typography

界面采用黑体字体栈，书摘与图形封面书名采用宋体字体栈。元信息为 12px，辅助标签为 11px；页内原文预览为 16px、两倍行高。Noto 字体依赖本机安装，尚未自托管，其他电脑可能回退。封面书名是可选择的 HTML 文字，不在图片中烘焙。

## Layout

仅覆盖宽度 900px 及以上的 PC 视口，页面整体纵向滚动。默认固定侧栏 204px，主区上边距 53px、左右内边距见 token；两列作品，每列依次展示封面、信息、书摘和操作。封面展台高 336px，封面为 198×280px。

900–1150px：侧栏 176px、主区左右内边距 32px、列距 25px；展台高 302px、封面 179×253px，页面标题 39px。1450px 起：主区左右内边距 70px、列距 46px；展台高 380px、封面 221×312px。小于 900px 没有移动端布局，不能据此声称适配手机。

## Elevation & Depth

主要界面依靠底色与细分隔线区分。封面使用轻微旋转、书脊细线和阴影模拟书籍体积；具体阴影与动效记录于 `design.json`。

## Shapes

操作与导航使用小圆角；书封接近直角，展台为平面矩形。预览面板采用圆角与细边框。

## Components

- 图形封面使用本目录编写的 `assets/rain-lines.svg`，文字封面使用 CSS 与 HTML 排版；本提案未使用生成图片素材。
- 作品局部 hover 或内部焦点使封面回正、上移 5px；主按钮 hover 加深。焦点轮廓为 2px 钴蓝，偏移 5px。减少动态效果偏好关闭过渡及平滑滚动。
- 只有两部虚构演示作品。书名搜索与准备状态筛选共同生效；可清除筛选，查看原文片段或准备说明并关闭预览。状态为静态样例，没有轮询或后台处理。
- 原文预览在当前页展开，关闭后恢复触发按钮焦点。标签库、标注库与原文检索入口禁用；旧版原型链接单独保留。没有业务数据库连接、写入或持久化。

## Do's and Don'ts

- Do：同时保留图形封面与无图文字封面的完整展示，用真实中文文字检查排版。
- Do：按当前实现和实际浏览器结果判断草案；颜色与尺寸仅在本目录范围有效。
- Don't：将双列、两个演示书名或当前封面构图直接当作大量作品、长书名或生产页面的已验证规则。
- Don't：把本页原文预览视为完整阅读与标注工作区。
