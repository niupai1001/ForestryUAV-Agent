---
id: canopy-metrics-and-ecology
title: 林冠覆盖、闭合度与森林结构指标
summary: 区分俯视冠盖度、地面仰视闭合度、单木数量、叶面积指数等不同测量对象。
tags: [林冠, 树冠, 冠盖度, 郁闭度, 覆盖率, canopy cover, canopy closure, lai, 叶面积指数, 林分结构]
applies_to: [code_run, segment_canopy, summarize_forest_structure]
version: 1
---

# 林冠覆盖、闭合度与森林结构指标

林冠覆盖度（canopy cover）通常指树冠垂直投影占地面面积的比例；林冠闭合度（canopy closure）指从某个地面点仰视时树冠遮挡天空半球的比例。二者视角与采样单位不同，不能混称。俯视正射影像更直接支持前者；仰视鱼眼照片、冠层分析仪等用于后者。不同法规或调查规程对“郁闭度”的定义可能另有规定，报告时写出操作性定义。

“树冠覆盖率”也不等于“植被覆盖率”：后者可包含灌木、草本与作物；不等于叶面积指数，后者表达单位地表面积上的叶面积；也不等于林木株数。树冠重叠时，覆盖率按**投影并集**计一次，不能把各单木冠幅面积简单相加后除以地块面积。

研究区和有效区需要预先确定。仅对正射图有效像元取分母会得到“影像有效区覆盖率”；若用户要求整个地块覆盖率，地块内的缺测区域需要单独标注，不能静默缩小分母。跨时相比较须使用一致的研究区、树冠定义、季节/物候和质量标准。

## 依据

- Jennings 等，《Assessing forest canopies and understorey illumination: canopy closure, canopy cover and other measures》，*Forestry* (1999)：<https://www2.cifor.org/mla/download/publication/Assessing%20canopies.pdf>。
- Korhonen 等，《Estimation of forest canopy cover: a comparison of field measurement techniques》，*Silva Fennica* (2006)：<https://www.silvafennica.fi/pdf/315>。
