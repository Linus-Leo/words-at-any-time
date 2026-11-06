/* FloatingWordBall 官网交互：胶囊演示 + 主题切换 + 提示 */
(function () {
  "use strict";

  var capsule = document.getElementById("capsule");
  if (!capsule) return;

  var wordEl = document.getElementById("capWord");
  var starsEl = document.getElementById("capStars");
  var defEl = document.getElementById("capDef");
  var noteEl = document.getElementById("capNote");
  var toastEl = document.getElementById("capToast");
  var headEl = document.getElementById("capHead");
  var bodyEl = document.getElementById("capBody");

  var WORDS = [
    { w: "serendipity", def: "n. 意外发现美好事物的运气；机缘巧合", stars: 2 },
    { w: "ephemeral", def: "adj. 短暂的；转瞬即逝的", stars: 3 },
    { w: "resilient", def: "adj. 有韧性的；能迅速恢复的", stars: 3 },
    { w: "solace", def: "n. 慰藉；安慰", stars: 2 },
    { w: "wanderlust", def: "n. 漫游癖；对旅行的强烈渴望", stars: 1 },
    { w: "luminous", def: "adj. 发光的；明亮的；睿智的", stars: 2 }
  ];

  var THEME_LABEL = { green: "青绿", blue: "湖蓝", amber: "琥珀", violet: "靛紫" };
  var idx = 0;
  var pinned = true;
  var toastTimer = null;

  function renderStars(n) {
    var html = "";
    for (var i = 0; i < 3; i++) {
      html += '<span class="s' + (i < n ? " on" : "") + '">★</span>';
    }
    starsEl.innerHTML = html;
    starsEl.setAttribute("aria-label", "词频重要度 " + n + " 星");
  }

  function renderWord() {
    var item = WORDS[idx % WORDS.length];
    wordEl.textContent = item.w;
    defEl.textContent = item.def;
    renderStars(item.stars);
  }

  function toast(msg) {
    toastEl.textContent = msg;
    toastEl.classList.add("show");
    if (toastTimer) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () {
      toastEl.classList.remove("show");
    }, 2100);
  }

  function setOpen(open) {
    capsule.classList.toggle("open", !!open);
  }

  // 悬停展开 / 移开收起（未固定时）
  capsule.addEventListener("mouseenter", function () { setOpen(true); });
  capsule.addEventListener("mouseleave", function () {
    if (!pinned) setOpen(false);
  });

  // 点击标题：固定 / 取消固定
  if (headEl) {
    headEl.addEventListener("click", function () {
      pinned = !pinned;
      capsule.classList.toggle("pinned", pinned);
      setOpen(pinned ? true : capsule.classList.contains("open"));
      toast(pinned ? "已固定释义，移开鼠标也不会收起" : "已取消固定，移开即收起");
    });
  }

  // 三档评级
  var RATE_MSG = {
    known: "认识 · 这个词今天不再出现",
    fuzzy: "模糊 · 排到后面第 2 位，继续出现",
    unknown: "不认识 · 排到第 1 位，并加入我的单词本"
  };
  Array.prototype.forEach.call(capsule.querySelectorAll(".rate"), function (btn) {
    btn.addEventListener("click", function (e) {
      e.stopPropagation();
      var rate = btn.getAttribute("data-rate");
      toast(RATE_MSG[rate] || "已记录");
      idx = (idx + 1) % WORDS.length;
      setTimeout(renderWord, 160);
    });
  });

  // 立方体加号：加入单词本
  var plus = capsule.querySelector(".cap-plus");
  if (plus) {
    plus.addEventListener("click", function (e) {
      e.stopPropagation();
      toast("已加入「我的单词本」");
    });
  }

  // 发音
  var speaker = capsule.querySelector(".cap-speaker");
  if (speaker) {
    speaker.addEventListener("click", function (e) {
      e.stopPropagation();
      toast("正在播放发音…");
    });
  }

  // 主题切换
  Array.prototype.forEach.call(document.querySelectorAll(".swatch"), function (sw) {
    sw.addEventListener("click", function () {
      var theme = sw.getAttribute("data-theme");
      capsule.setAttribute("data-theme", theme);
      Array.prototype.forEach.call(document.querySelectorAll(".swatch"), function (o) {
        o.setAttribute("aria-pressed", o === sw ? "true" : "false");
      });
      toast("已切换到「" + (THEME_LABEL[theme] || theme) + "」");
    });
  });

  // 截图缺失时显示占位（onerror 已处理，这里再兜底一次）
  Array.prototype.forEach.call(document.querySelectorAll(".shot-frame img"), function (img) {
    if (img.complete && img.naturalWidth === 0) {
      img.closest(".shot-frame").classList.add("is-empty");
    }
  });

  renderWord();
})();
