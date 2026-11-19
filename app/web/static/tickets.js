function ticketPanel(container, request, {admin = false} = {}) {
  let offset = 0;
  let selected = null;
  const list = container.querySelector("[data-ticket-list]");
  const detail = container.querySelector("[data-ticket-detail]");
  const status = container.querySelector("[data-ticket-status]");
  const body = container.querySelector("[data-ticket-body]");
  const reply = container.querySelector("[data-ticket-reply]");
  const close = container.querySelector("[data-ticket-close]");
  const create = container.querySelector("[data-ticket-create]");
  const next = container.querySelector("[data-ticket-next]");
  const previous = container.querySelector("[data-ticket-prev]");

  async function load() {
    const page = await request("?offset=" + offset);
    list.replaceChildren();
    for (const item of page.items) {
      const button = document.createElement("button");
      button.type = "button";
      button.className = "secondary-btn";
      button.textContent = `№${item.id} · ${item.subject}${item.closed ? " · закрыт" : ""}`;
      button.onclick = () => perform(() => show(item.id));
      list.append(button);
    }
    if (!page.items.length) list.textContent = "Обращений пока нет.";
    next.disabled = !page.more;
    previous.disabled = offset === 0;
  }

  async function show(id) {
    selected = id;
    const item = await request("/" + id);
    if (selected !== id) return;
    container.querySelector("[data-ticket-heading]").textContent =
      `№${id} · ${item.subject}${admin ? " · Telegram " + item.telegram_id : ""}`;
    body.replaceChildren();
    for (const message of item.replies) {
      const line = document.createElement("article");
      const heading = document.createElement("strong");
      heading.textContent = (message.from_admin ? "Поддержка" : "Пользователь") + " · " +
        new Date(message.created_at).toLocaleString("ru-RU");
      const text = document.createElement("p");
      text.className = "ticket-message";
      text.textContent = message.body;
      line.append(heading, text);
      body.append(line);
    }
    reply.hidden = item.closed;
    if (close) {
      close.textContent = item.closed ? "Открыть снова" : "Закрыть тикет";
      close.onclick = () => perform(async () => {
        await request("/" + id, {closed: !item.closed}, "PUT");
        await Promise.all([show(id), load()]);
      }, close);
    }
    detail.hidden = false;
  }

  async function perform(action, button) {
    status.textContent = "";
    if (button) button.disabled = true;
    try {
      await action();
    } catch (error) {
      status.textContent = error.message;
    } finally {
      if (button) button.disabled = false;
    }
  }

  if (create) create.onsubmit = event => {
    event.preventDefault();
    perform(async () => {
      const item = await request("", Object.fromEntries(new FormData(create)), "POST");
      create.reset();
      offset = 0;
      await Promise.all([show(item.id), load()]);
    }, event.submitter);
  };
  reply.onsubmit = event => {
    event.preventDefault();
    const id = selected;
    perform(async () => {
      await request("/" + id + "/replies", Object.fromEntries(new FormData(reply)), "POST");
      reply.reset();
      if (selected === id) await show(id);
    }, event.submitter);
  };
  next.onclick = () => perform(async () => { offset += 50; await load(); });
  previous.onclick = () => perform(async () => { offset -= 50; await load(); });
  return () => perform(load);
}
