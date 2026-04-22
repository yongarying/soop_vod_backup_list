const state = {
  snapshot: null,
  filter: "all",
  search: "",
  view: "vods",
  page: 1,
  completionPage: 1,
  completionNextIndexOnPage: 1,
};
const POLL_INTERVAL_MS = 60000;
const PAGE_SIZE = 60;
const PAGE_BUTTON_COUNT = 10;

const filterDefinitions = [
  { key: "all", label: "전체" },
  { key: "permanent", label: "영구보관" },
  { key: "policy_day", label: "6월 1일 삭제" },
  { key: "soon", label: "정책 시행 후 90일 이내 삭제" },
  { key: "other", label: "나머지" },
  { key: "confirmed", label: "별풍 확인" },
  { key: "views_900_plus", label: "순수조회 900회 이상" },
  { key: "views_1000_plus", label: "순수조회 1000회 초과" },
];

async function fetchJson(url, options = {}) {
  const response = await fetch(url, {
    headers: {
      "Content-Type": "application/json",
    },
    ...options,
  });
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json();
}

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => {
    switch (char) {
      case "&":
        return "&amp;";
      case "<":
        return "&lt;";
      case ">":
        return "&gt;";
      case '"':
        return "&quot;";
      case "'":
        return "&#39;";
      default:
        return char;
    }
  });
}

function sanitizeUrl(value, fallback = "") {
  if (!value) return fallback;
  try {
    const url = new URL(String(value), window.location.origin);
    if (url.protocol === "http:" || url.protocol === "https:") {
      return url.toString();
    }
  } catch (error) {
    return fallback;
  }
  return fallback;
}

function number(value) {
  return new Intl.NumberFormat("ko-KR").format(value || 0);
}

function formatDateTime(value) {
  if (!value) return "-";
  const date = new Date(value);
  return new Intl.DateTimeFormat("ko-KR", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatUploadDateTime(value) {
  if (!value) return "-";
  return value.replace("T", " ");
}

function renderHeader(snapshot) {
  document.title = snapshot.page_title || "캬앙 다시보기 살리기 캠페인";
  document.getElementById("pageHeading").textContent = snapshot.page_heading || "캬앙 다시보기 살리기 캠페인";
}

function policyLabel(vod) {
  const policyDate = state.snapshot?.policy_date || "2026-06-01";
  if (vod.future_permanent) return "영구보관";
  if (vod.delete_on_policy_day) return `${policyDate} 삭제`;
  return `${vod.future_expiry_date} 만료`;
}

function policyReasonLabel(vod) {
  switch (vod.future_reason) {
    case "partner_permanent":
      return "파트너 스트리머 영구보관";
    case "pre_policy_support_confirmed":
      return "";
    case "best_views_over_1000":
      return "";
    case "best_basic_2_years":
      return "";
    case "general_views_50_plus_1_year":
      return "순수조회 50회 이상으로 1년 저장";
    case "general_basic_90_days":
    default:
      return "기본 90일 저장";
  }
}

function viewPermanentReasonLabel(vod) {
  if (vod.views_1000_plus) {
    return "베스트 스트리머 조회수 1000회 초과";
  }
  return "";
}

function autoSupportDetail(vod) {
  if (!vod.auto_support_confirmed) return "";
  const supportName = vod.auto_support_kind === "adballoon" ? "애드벌룬" : "별풍선";
  const nick = String(vod.auto_support_user_nick || "").trim();
  const userId = String(vod.auto_support_user_id || "").trim();
  const actor = nick && userId ? `${nick}(${userId})` : nick || (userId ? `(${userId})` : "후원자");
  return `${actor} ${supportName} ${number(vod.auto_support_amount)}개 확인`;
}

function autoSupportCompletedAt(vod) {
  if (!vod.auto_support_confirmed || !vod.auto_support_reg_date) return "";
  return `${formatUploadDateTime(vod.auto_support_reg_date)} 완료`;
}

function policyDetailLines(vod) {
  return [autoSupportDetail(vod), autoSupportCompletedAt(vod), viewPermanentReasonLabel(vod), policyReasonLabel(vod)].filter(
    Boolean
  );
}

function metaRows(snapshot) {
  return [["마지막 갱신", formatDateTime(snapshot.generated_at)]];
}

function renderMeta(snapshot) {
  const panel = document.getElementById("metaPanel");
  panel.innerHTML = metaRows(snapshot)
    .map(
      ([label, value]) => `
        <div class="meta-row">
          <dt>${escapeHtml(label)}</dt>
          <dd>${escapeHtml(value)}</dd>
        </div>
      `
    )
    .join("");
}

function renderViewState() {
  const isRanking = state.view === "ranking";
  document.getElementById("rankingButton").textContent = isRanking ? "다시보기 목록" : "참여자 랭킹";
  document.getElementById("vodToolbar").classList.toggle("hidden", isRanking);
  document.getElementById("vodCard").classList.toggle("hidden", isRanking);
  document.getElementById("rankingCard").classList.toggle("hidden", !isRanking);
}

function renderFilters(snapshot) {
  const summary = snapshot.summary || {};
  const counts = {
    all: summary.total || 0,
    permanent: summary.future_permanent || 0,
    policy_day: summary.policy_day_delete || 0,
    soon: summary.soon_after_policy || 0,
    other: summary.other_count || 0,
    confirmed: summary.confirmed || 0,
    views_900_plus: summary.views_900_plus || 0,
    views_1000_plus: summary.views_1000_plus || 0,
  };

  document.getElementById("filterBar").innerHTML = filterDefinitions
    .map(
      (filter) => `
        <button class="filter-button ${state.filter === filter.key ? "active" : ""}" type="button" data-filter="${filter.key}">
          <span>${escapeHtml(filter.label)}</span>
          <span class="filter-count">${number(counts[filter.key])}</span>
        </button>
      `
    )
    .join("");
}

function filteredVods(snapshot) {
  const query = state.search.trim().toLowerCase();
  return snapshot.vods.filter((vod) => {
    const title = String(vod.title_name || "").toLowerCase();
    if (query && !title.includes(query)) {
      return false;
    }

    switch (state.filter) {
      case "all":
        return true;
      case "permanent":
        return vod.future_permanent;
      case "policy_day":
        return vod.delete_on_policy_day;
      case "soon":
        return vod.urgency === "soon";
      case "other":
        return !vod.future_permanent && !vod.delete_on_policy_day && vod.urgency !== "soon" && !vod.support_confirmed;
      case "confirmed":
        return vod.support_confirmed;
      case "views_900_plus":
        return vod.views_900_plus;
      case "views_1000_plus":
        return vod.views_1000_plus;
      default:
        return true;
    }
  });
}

function safePlayerUrl(vod) {
  return sanitizeUrl(vod.player_url, "#");
}

function safeThumbnailUrl(vod) {
  return sanitizeUrl(vod.thumbnail_url, "");
}

function renderTable(snapshot) {
  const vods = filteredVods(snapshot);
  const title = document.getElementById("tableTitle");
  const completionStatusElement = document.getElementById("completionStatus");
  const body = document.getElementById("vodTableBody");
  const totalPages = Math.max(1, Math.ceil(vods.length / PAGE_SIZE));

  if (state.page > totalPages) state.page = totalPages;
  if (state.page < 1) state.page = 1;

  const startIndex = (state.page - 1) * PAGE_SIZE;
  const pageVods = vods.slice(startIndex, startIndex + PAGE_SIZE);

  const activeFilter = filterDefinitions.find((item) => item.key === state.filter);
  title.textContent = activeFilter ? activeFilter.label : "전체";
  const completion = completionStatus(snapshot);
  state.completionPage = completion.page;
  state.completionNextIndexOnPage = completion.nextIndexOnPage;
  completionStatusElement.textContent = completion.label;
  completionStatusElement.disabled = !completion.page;
  completionStatusElement.classList.toggle("hidden", !completion.label);
  renderPagination(vods.length, totalPages);

  if (vods.length === 0) {
    body.innerHTML = document.getElementById("emptyStateTemplate").innerHTML;
    return;
  }

  body.innerHTML = pageVods
    .map(
      (vod, index) => {
        const policyDetails = policyDetailLines(vod);
        const pageItemNumber = index + 1;
        return `
        <tr data-page-index="${pageItemNumber}">
          <td class="cell-index">
            ${number(pageItemNumber)}
          </td>
          <td class="cell-upload">
            <div class="mono-copy">${escapeHtml(formatUploadDateTime(vod.uploaded_at))}</div>
            <div class="mini-copy">${escapeHtml(vod.duration_label)}</div>
          </td>
          <td class="cell-title">
            <div class="vod-title">
              <a class="vod-thumb" href="${escapeHtml(safePlayerUrl(vod))}" target="_blank" rel="noreferrer">
                <img src="${escapeHtml(safeThumbnailUrl(vod))}" alt="" loading="lazy" />
              </a>
              <div class="vod-body">
                <h3><a href="${escapeHtml(safePlayerUrl(vod))}" target="_blank" rel="noreferrer">${escapeHtml(vod.title_name)}</a></h3>
                <div class="inline-meta">
                  ${vod.future_permanent ? `<span class="badge safe">영구보관</span>` : ""}
                  ${vod.delete_on_policy_day ? `<span class="badge danger">6월 1일 삭제</span>` : ""}
                  ${vod.views_900_plus ? `<span class="badge">순수조회 900+</span>` : ""}
                  ${vod.views_1000_plus ? `<span class="badge safe">순수조회 1000회 초과</span>` : ""}
                </div>
              </div>
            </div>
          </td>
          <td class="cell-metrics">
            <div class="status-stack">
              <span class="badge ${vod.pure_views > 1000 ? "safe" : ""}">순수조회 ${number(vod.pure_views)}</span>
              <span class="badge">표시조회 ${number(vod.display_views)}</span>
              ${vod.merged_view_count_applies ? `<span class="badge">라이브참여 ${number(vod.estimated_live_views)}</span>` : ""}
              <span class="badge">댓글 ${number(vod.comment_count)}</span>
            </div>
          </td>
          <td class="cell-policy">
            <div class="status-stack">
              <span class="badge ${vod.future_permanent ? "safe" : "danger"}">${escapeHtml(policyLabel(vod))}</span>
            </div>
            ${policyDetails.map((line) => `<div class="mini-copy">${escapeHtml(line)}</div>`).join("")}
          </td>
        </tr>
      `;
      }
    )
    .join("");
}

function completionStatus(snapshot) {
  if (state.filter !== "all") return { label: "", page: 0, nextIndexOnPage: 0 };
  const vods = snapshot.vods || [];
  let completedCount = 0;

  for (const vod of vods) {
    if (!vod.future_permanent) break;
    completedCount += 1;
  }

  if (completedCount === 0) {
    return { label: "완료(클릭하면 이동) : 아직 없음", page: 0, nextIndexOnPage: 0 };
  }

  const completedPage = Math.ceil(completedCount / PAGE_SIZE);
  const completedIndexOnPage = ((completedCount - 1) % PAGE_SIZE) + 1;
  const nextIndex = Math.min(completedCount + 1, vods.length);
  const targetPage = Math.max(1, Math.ceil(nextIndex / PAGE_SIZE));
  const nextIndexOnPage = ((nextIndex - 1) % PAGE_SIZE) + 1;
  return {
    label: `완료(클릭하면 이동) : ${number(completedPage)}페이지 ${number(completedIndexOnPage)}번 VOD`,
    page: targetPage,
    nextIndexOnPage,
  };
}

function renderPagination(totalItems, totalPages) {
  const containers = [document.getElementById("paginationTop"), document.getElementById("paginationBottom")].filter(Boolean);
  if (!containers.length) return;
  if (totalItems <= PAGE_SIZE) {
    containers.forEach((container) => {
      container.innerHTML = "";
    });
    return;
  }

  const pageGroupStart = Math.floor((state.page - 1) / PAGE_BUTTON_COUNT) * PAGE_BUTTON_COUNT + 1;
  const pageGroupEnd = Math.min(pageGroupStart + PAGE_BUTTON_COUNT - 1, totalPages);
  const pageButtons = [];
  for (let page = pageGroupStart; page <= pageGroupEnd; page += 1) {
    pageButtons.push(`
      <button class="pagination-button ${page === state.page ? "active" : ""}" type="button" data-page="${page}">
        ${number(page)}
      </button>
    `);
  }

  const markup = `
    <button class="pagination-button" type="button" data-page="first" ${state.page <= 1 ? "disabled" : ""}>처음</button>
    <button class="pagination-button" type="button" data-page="prev" ${state.page <= 1 ? "disabled" : ""}>&lt;</button>
    ${pageButtons.join("")}
    <button class="pagination-button" type="button" data-page="next" ${state.page >= totalPages ? "disabled" : ""}>&gt;</button>
    <button class="pagination-button" type="button" data-page="last" ${state.page >= totalPages ? "disabled" : ""}>끝</button>
    <span class="pagination-current">${number(state.page)} / ${number(totalPages)}</span>
  `;

  containers.forEach((container) => {
    container.innerHTML = markup;
  });
}

function renderRanking(snapshot) {
  const ranking = [...(snapshot.participant_ranking || [])].sort(
    (a, b) =>
      numberValue(b.total_starballoons) - numberValue(a.total_starballoons) ||
      String(a.user_nick || "").localeCompare(String(b.user_nick || ""), "ko-KR") ||
      String(a.user_id || "").localeCompare(String(b.user_id || ""), "ko-KR")
  );
  const caption = document.getElementById("rankingCaption");
  const body = document.getElementById("rankingTableBody");
  const startDate = snapshot.participant_ranking_start_date || "2026-04-15";

  caption.textContent = `${startDate} 이후 별풍선 합산 · ${number(ranking.length)}명`;

  if (ranking.length === 0) {
    body.innerHTML = document.getElementById("emptyRankingTemplate").innerHTML;
    return;
  }

  body.innerHTML = ranking
    .map(
      (participant, index) => `
        <tr>
          <td class="cell-rank mono-copy">${number(index + 1)}</td>
          <td>${escapeHtml(participant.user_nick || "-")}</td>
          <td class="mono-copy">${escapeHtml(participant.user_id || "-")}</td>
          <td class="mono-copy">${number(participant.total_starballoons)}</td>
        </tr>
      `
    )
    .join("");
}

function numberValue(value) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

function render() {
  if (!state.snapshot) return;
  renderHeader(state.snapshot);
  renderMeta(state.snapshot);
  renderViewState();
  renderFilters(state.snapshot);
  renderTable(state.snapshot);
  renderRanking(state.snapshot);
}

async function loadSnapshot() {
  state.snapshot = await fetchJson("/api/status");
  render();
}

document.getElementById("filterBar").addEventListener("click", (event) => {
  const button = event.target.closest("[data-filter]");
  if (!button) return;
  state.filter = button.dataset.filter;
  state.page = 1;
  render();
});

document.getElementById("rankingButton").addEventListener("click", () => {
  state.view = state.view === "ranking" ? "vods" : "ranking";
  render();
});

document.getElementById("searchInput").addEventListener("input", (event) => {
  state.search = event.target.value || "";
  state.page = 1;
  render();
});

document.getElementById("completionStatus").addEventListener("click", () => {
  if (!state.snapshot || !state.completionPage) return;
  state.filter = "all";
  state.search = "";
  state.page = state.completionPage;
  document.getElementById("searchInput").value = "";
  render();
  requestAnimationFrame(() => {
    const row = document.querySelector(`#vodTableBody [data-page-index="${state.completionNextIndexOnPage}"]`);
    if (!row) return;
    row.scrollIntoView({ behavior: "smooth", block: "center" });
    row.classList.add("row-highlight");
    window.setTimeout(() => row.classList.remove("row-highlight"), 1800);
  });
});

document.getElementById("vodCard").addEventListener("click", (event) => {
  const button = event.target.closest("[data-page]");
  if (!button || button.disabled || !state.snapshot) return;
  const totalPages = Math.max(1, Math.ceil(filteredVods(state.snapshot).length / PAGE_SIZE));

  const targetPage = Number(button.dataset.page);
  if (Number.isInteger(targetPage) && targetPage >= 1) {
    state.page = Math.min(totalPages, targetPage);
    render();
    return;
  }

  switch (button.dataset.page) {
    case "first":
      state.page = 1;
      break;
    case "prev":
      state.page = Math.max(1, state.page - 1);
      break;
    case "next":
      state.page = Math.min(totalPages, state.page + 1);
      break;
    case "last":
      state.page = totalPages;
      break;
    default:
      break;
  }
  render();
});

loadSnapshot();
setInterval(loadSnapshot, POLL_INTERVAL_MS);
