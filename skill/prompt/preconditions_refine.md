# 前置条件精修（Preconditions Refine）模板

任务：在给定骨架上进行保守增删，避免过拟合；返回精修后的 preconditions 与简短说明。

## 输入（占位符）
- 骨架：{skeleton_preconditions_json}
- 信号：
  - 遮挡命中：{signals.overlay_hits_json}
  - 可见性/遮挡比：{signals.visible_adv}, {signals.occlusion_ratio_avg}
  - 视口：{meta.viewport_json}
  - 是否移动端：{is_mobile_bool}
- 定位器：{locators.selector}

## 输出（占位符，JSON）
```json
{
  "preconditions": { /* refined object */ },
  "notes": "<string>"
}
```
