/** Project home page controller. */

import { api, ApiError } from "./api.js";
import { ensureAuth, devLogin } from "./auth.js";
import { confirmAction, haptic } from "./tg.js";
import { toPersianDigits } from "./state.js";

const els = {
  userChip: document.getElementById("user-chip"),
  btnNew: document.getElementById("btn-new-project"),
  btnDevLogin: document.getElementById("btn-dev-login"),
  btnRefresh: document.getElementById("btn-refresh"),
  grid: document.getElementById("project-grid"),
  empty: document.getElementById("projects-empty"),
  loading: document.getElementById("projects-loading"),
};

let user = null;

function toast(message, type = "") {
  const wrap = document.getElementById("toast-wrap");
  const el = document.createElement("div");
  el.className = `toast ${type}`;
  el.textContent = message;
  wrap.appendChild(el);
  setTimeout(() => el.remove(), 3800);
}

function formatUpdated(iso) {
  try {
    return new Intl.DateTimeFormat("fa-IR", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(new Date(iso));
  } catch {
    return iso;
  }
}

function renderProjects(projects) {
  els.grid.innerHTML = "";
  els.empty.classList.toggle("hidden", projects.length > 0);
  els.loading.classList.add("hidden");

  for (const project of projects) {
    const card = document.createElement("article");
    card.className = "project-card";

    const name = document.createElement("div");
    name.className = "name";
    name.textContent = project.title;

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.innerHTML = `
      <span>به‌روزرسانی: ${formatUpdated(project.updated_at)}</span>
      <span>${toPersianDigits(project.asset_count)} فایل</span>
      <span>${toPersianDigits(project.clip_count)} کلیپ</span>
    `;

    const actions = document.createElement("div");
    actions.className = "actions";

    const openBtn = document.createElement("button");
    openBtn.className = "btn btn-primary";
    openBtn.textContent = "باز کردن";
    openBtn.addEventListener("click", () => {
      haptic("light");
      window.location.href = `/editor/?p=${project.id}`;
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn-danger";
    deleteBtn.textContent = "حذف";
    deleteBtn.addEventListener("click", async () => {
      const ok = await confirmAction(
        `پروژه «${project.title}» و همه فایل‌های آن برای همیشه حذف شود؟`,
      );
      if (!ok) return;
      try {
        await api.deleteProject(project.id);
        haptic("medium");
        toast("پروژه حذف شد.", "success");
        loadProjects();
      } catch (err) {
        toast(err.message || "حذف پروژه ناموفق بود.", "error");
      }
    });

    actions.append(openBtn, deleteBtn);
    card.append(name, meta, actions);
    els.grid.appendChild(card);
  }
}

async function loadProjects() {
  els.loading.classList.remove("hidden");
  try {
    const data = await api.listProjects();
    renderProjects(data.results || data);
  } catch (err) {
    els.loading.classList.add("hidden");
    toast(err.message || "بارگذاری پروژه‌ها ناموفق بود.", "error");
  }
}

async function createProject() {
  const title = window.prompt("عنوان پروژه:", "ریمیکس جدید");
  if (title === null) return;
  els.btnNew.disabled = true;
  try {
    const project = await api.createProject(title.trim() || "پروژه بدون عنوان");
    haptic("medium");
    window.location.href = `/editor/?p=${project.id}`;
  } catch (err) {
    toast(err.message || "ساخت پروژه ناموفق بود.", "error");
    els.btnNew.disabled = false;
  }
}

async function boot() {
  const auth = await ensureAuth();

  if (auth.user) {
    user = auth.user;
    els.userChip.textContent = user.first_name || user.username || "کاربر";
    await loadProjects();
  } else {
    els.userChip.textContent = "وارد نشده";
    els.loading.classList.add("hidden");
    els.empty.classList.remove("hidden");
    if (auth.devEnabled) {
      els.btnDevLogin.classList.remove("hidden");
      els.btnDevLogin.addEventListener("click", async () => {
        try {
          user = await devLogin("devuser");
          els.userChip.textContent = user.first_name || user.username;
          els.btnDevLogin.classList.add("hidden");
          await loadProjects();
        } catch (err) {
          toast(err.message || "ورود ناموفق بود.", "error");
        }
      });
    }
  }
}

els.btnNew.addEventListener("click", createProject);
els.btnRefresh.addEventListener("click", () => {
  if (user) loadProjects();
});

boot();
