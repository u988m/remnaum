function ticketId(value) {
  if (!/^[1-9]\d{0,9}$/.test(value ?? "")) return null;
  const id = Number(value);
  return id <= 2147483647 ? id : null;
}

function ticketPanel(container, request, {admin = false, openTicket, openHistory}) {
  const el = name => container.querySelector(`[data-ticket-${name}]`);
  const list = el("list"), thread = el("body"), reply = el("reply"), create = el("create");
  const close = el("close"), searchForm = el("search");
  const dates = new Intl.DateTimeFormat("ru-RU", {dateStyle: "medium", timeStyle: "short"});
  const formatDate = value => dates.format(new Date(value));
  const drafts = new Map();
  let selected = null;
  let loaded = null;
  let lastMessageId = null;
  let listRequest = 0;
  let detailRequest = 0;
  let busy = false;
  let offset = 0;
  let pageSize = 20;
  let filter = "all";
  let search = "";

  function setBadge(badge, closed) {
    badge.textContent = closed ? "Закрыто" : "Открыто";
    badge.dataset.closed = String(closed);
  }

  function markSelected() {
    for (const button of list.querySelectorAll("[data-ticket-id]")) {
      if (Number(button.dataset.ticketId) === selected) button.setAttribute("aria-current", "true");
      else button.removeAttribute("aria-current");
    }
  }

  async function loadList() {
    const requestId = ++listRequest;
    el("prev").disabled = el("next").disabled = true;
    el("list-status").textContent = "Загружаем историю…";
    try {
      const params = new URLSearchParams({offset, state: filter, search});
      const page = await request("?" + params);
      if (requestId !== listRequest) return;
      pageSize = page.page_size;
      if (offset && !page.items.length) {
        offset = Math.max(0, offset - pageSize);
        return loadList();
      }
      list.replaceChildren();
      for (const item of page.items) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "ticket-row";
        button.dataset.ticketId = item.id;
        button.onclick = () => openTicket(item.id);
        const top = document.createElement("span");
        top.className = "ticket-row-top";
        const number = document.createElement("span");
        number.textContent = `№${item.id}`;
        const badge = document.createElement("span");
        badge.className = "ticket-badge";
        setBadge(badge, item.closed);
        top.append(number, badge);
        const subject = document.createElement("strong");
        subject.textContent = item.subject;
        const meta = document.createElement("span");
        meta.className = "muted";
        meta.textContent = (admin ? `Клиент ${item.telegram_id} · ` : "") + formatDate(item.updated_at);
        button.append(top, subject, meta);
        list.append(button);
      }
      markSelected();
      el("pages").hidden = offset === 0 && !page.more;
      el("page").textContent = "Страница " + (Math.floor(offset / pageSize) + 1);
      el("prev").disabled = offset === 0;
      el("next").disabled = !page.more;
      el("list-status").textContent = page.items.length ? ""
        : search || filter !== "all" ? "По выбранным условиям обращений нет." : "Обращений пока нет.";
    } catch (error) {
      if (requestId === listRequest) el("list-status").textContent = error.message;
    }
  }

  async function loadDetail(id, {scroll = false} = {}) {
    const requestId = ++detailRequest;
    const status = el("detail-status");
    status.textContent = "Загружаем обращение…";
    try {
      const item = await request("/" + id);
      if (requestId !== detailRequest || selected !== id) return;
      const latest = item.replies.at(-1)?.id;
      const atBottom = thread.scrollHeight - thread.scrollTop - thread.clientHeight < 30;
      const oldScroll = thread.scrollTop;
      if (loaded?.id !== id || latest !== lastMessageId) {
        thread.replaceChildren();
        for (const message of item.replies) {
          const line = document.createElement("article");
          line.className = "ticket-bubble";
          line.classList.toggle("is-own", message.from_admin === admin);
          const heading = document.createElement("div");
          heading.className = "ticket-message-meta";
          const author = document.createElement("strong");
          author.textContent = message.from_admin === admin ? "Вы" : message.from_admin ? "Поддержка" : "Клиент";
          const date = document.createElement("time");
          date.dateTime = message.created_at;
          date.textContent = formatDate(message.created_at);
          heading.append(author, date);
          const text = document.createElement("p");
          text.className = "ticket-message";
          text.textContent = message.body;
          line.append(heading, text);
          thread.append(line);
        }
      }
      loaded = item;
      lastMessageId = latest;
      el("heading").textContent = item.subject;
      el("number").textContent = `Тикет №${id}`;
      el("meta").textContent = (admin ? `Клиент ${item.telegram_id} · ` : "") +
        "Создано " + formatDate(item.created_at);
      setBadge(el("state"), item.closed);
      reply.hidden = item.closed;
      el("closed-note").hidden = !item.closed;
      close.textContent = item.closed ? "Открыть снова" : "Закрыть обращение";
      close.classList.toggle("ticket-close-action", !item.closed);
      el("content").hidden = false;
      status.textContent = "";
      if (scroll && el("detail").getClientRects().length && !el("detail").contains(document.activeElement)) {
        el("heading").focus({preventScroll: true});
      }
      if (scroll || atBottom) thread.scrollTop = thread.scrollHeight;
      else thread.scrollTop = oldScroll;
    } catch (error) {
      if (requestId !== detailRequest || selected !== id) return;
      if (error.status === 404) {
        loaded = null;
        el("content").hidden = true;
      }
      status.textContent = error.message;
    }
  }

  function toggleCreate(open) {
    if (!create) return;
    create.hidden = !open;
    el("new").setAttribute("aria-expanded", String(open));
    el("new").disabled = open;
    if (open) create.elements.subject.focus();
  }

  async function render(id) {
    const next = ticketId(String(id ?? ""));
    const changed = next !== selected;
    if (changed && selected) drafts.set(selected, reply.elements.body.value);
    selected = next;
    markSelected();
    el("layout").classList.toggle("has-ticket", Boolean(selected));
    el("detail").hidden = !selected;
    el("placeholder").hidden = Boolean(selected);
    if (!selected) {
      ++detailRequest;
      loaded = null;
    } else if (changed) {
      toggleCreate(false);
      el("content").hidden = true;
      reply.elements.body.value = drafts.get(selected) || "";
    }
    await Promise.all([loadList(), selected ? loadDetail(selected, {scroll: changed}) : null]);
  }

  async function mutate(action, button, status) {
    if (busy) return;
    busy = true;
    button.disabled = true;
    status.textContent = "";
    try {
      await action();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      busy = false;
      button.disabled = false;
    }
  }

  if (create) {
    el("new").onclick = () => {
      if (selected) openHistory();
      toggleCreate(true);
    };
    el("cancel").onclick = () => {
      toggleCreate(false);
      el("new").focus();
    };
    create.onsubmit = event => {
      event.preventDefault();
      mutate(async () => {
        const item = await request("", Object.fromEntries(new FormData(create)), "POST");
        create.reset();
        toggleCreate(false);
        offset = 0;
        search = searchForm.elements.search.value = "";
        setFilter("all");
        openTicket(item.id);
      }, event.submitter, el("create-status"));
    };
  }

  reply.onsubmit = event => {
    event.preventDefault();
    const id = selected;
    mutate(async () => {
      await request("/" + id + "/replies", Object.fromEntries(new FormData(reply)), "POST");
      drafts.delete(id);
      if (selected === id) {
        reply.reset();
        await loadDetail(id, {scroll: true});
      }
      await loadList();
    }, event.submitter, el("detail-status"));
  };
  close.onclick = () => {
    const id = selected;
    if (!loaded || loaded.id !== id || (!loaded.closed && !window.confirm(`Закрыть обращение №${id}?`))) return;
    const closed = !loaded.closed;
    mutate(async () => {
      await request("/" + id, {closed}, "PUT");
      await Promise.all([loadList(), selected === id ? loadDetail(id) : null]);
    }, close, el("detail-status"));
  };
  el("back").onclick = openHistory;
  el("refresh").onclick = () => { if (selected) loadDetail(selected); };
  el("refresh-list").onclick = loadList;
  el("next").onclick = () => { offset += pageSize; loadList(); };
  el("prev").onclick = () => { offset = Math.max(0, offset - pageSize); loadList(); };

  function setFilter(value) {
    filter = value;
    offset = 0;
    for (const button of container.querySelectorAll("[data-ticket-filter]")) {
      button.setAttribute("aria-pressed", String(button.dataset.ticketFilter === filter));
    }
  }
  for (const button of container.querySelectorAll("[data-ticket-filter]")) {
    button.onclick = () => {
      setFilter(button.dataset.ticketFilter);
      loadList();
    };
  }
  searchForm.onsubmit = event => {
    event.preventDefault();
    search = searchForm.elements.search.value.trim();
    offset = 0;
    loadList();
  };
  searchForm.elements.search.addEventListener("search", () => {
    if (!searchForm.elements.search.value) searchForm.requestSubmit();
  });
  return render;
}
