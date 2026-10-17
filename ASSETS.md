本仓库素材说明（第三方素材 / Third-party assets）
================================================

1. 版权归属

   app/static/img/bili_logo.svg          —— 哔哩哔哩（bilibili）商标
   app/static/img/mascot_2233_door.png  —— 哔哩哔哩「22娘 & 33娘」官方形象
   app/static/img/mascot/m22_idea.webp  —— 哔哩哔哩「22娘」官方形象
   app/static/img/nodata.png            —— 抓取自目标站点的空状态图标

   _archive/ 目录下的同名副本与上述文件一致，仅用于页面结构还原的取证留档。

   上述素材的著作权及相关商标权均归哔哩哔哩所有，本项目不主张任何权利。

2. 收录目的

   本项目是对 bili.mssss.cn 前端界面的**还原研究**（详见 docs/MIRROR_REPORT.md）。
   为了证明还原结果与原站一致，将原始素材原样保留在 app/static/img/ 供逐像素比对。

3. 不适用 MIT 许可

   根目录 LICENSE 中的 MIT 条款**不覆盖**上述素材。整份许可证的适用范围以 LICENSE
   文件末尾的 "EXCLUDED ASSETS" 段落为准。

4. 使用者的义务

   - 商业使用、对外分发、公开部署前，必须将上述素材替换为你拥有合法权利的图形；
   - 不得以任何方式暗示本项目与哔哩哔哩存在合作、授权或关联关系；
   - 页面文案中出现的「哔哩哔哩」「B 站」「22娘 / 33娘」等指向性表述，建议一并改为中性描述。

5. 替换方法

   保持文件名与目录不变，直接覆盖即可，无需修改任何代码：

   app/static/img/bili_logo.svg          顶栏 / 页脚 logo，建议宽 140 × 高 64
   app/static/img/mascot/m22_idea.webp   首页插图，宽 ≤ 660
   app/static/img/mascot_2233_door.png   首页底部 CTA 图，建议 ≥ 380×380 透明底
   app/static/img/nodata.png             favicon 与空状态图，512×512

   CSS 中不含任何对这些图片像素尺寸的硬性依赖，替换后布局不会错位。

6. 移除请求

   若你是权利人或其授权代表，希望本项目移除上述素材，请通过仓库 Issue 提出，
   我们会在确认后立即删除对应文件并同步更新还原报告。
