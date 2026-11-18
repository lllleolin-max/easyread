/* 本地文件夹树和 PDF 的剪切、复制、粘贴。 */
(function (PR) {
  "use strict";
  const L = PR.lib, T = PR.catTree, side = PR.$("#side");
  const library = PR.$(".lib"), sideButton = PR.$("#libraryNavBtn");
  let clipboard = null, pasting = false;

  function showSide(open) {
    library.classList.toggle("library-nav-open", open);
    sideButton.setAttribute("aria-expanded", String(open));
  }
  sideButton.innerHTML = PR.icon("book", "sm");
  sideButton.onclick = () => showSide(!library.classList.contains("library-nav-open"));
  document.addEventListener("click", (e) => {
    if (library.classList.contains("library-nav-open") && !e.target.closest("#side, #libraryNavBtn, #ctxmenu")) showSide(false);
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && library.classList.contains("library-nav-open")) showSide(false);
  });

  function row(folder, kids, open) {
    const current = L.view === "folder" && L.tag === folder;
    return '<div class="srow tree' + (current ? " on" : "") + '" data-folder="' + PR.esc(folder) +
      '" tabindex="0" style="--lvl:' + T.depth(folder) + '" title="' + PR.esc(T.label(folder)) + '">' +
      PR.foldSlot(kids, open) + PR.icon("folder", "sm") + '<span class="t">' + PR.esc(T.leaf(folder)) +
      '</span><span class="n">' + L.items.filter((i) => L.inFolder(i, folder)).length +
      '</span><span class="more" data-more title="' + PR.t("更多") + '">' + PR.icon("more", "sm") + '</span></div>';
  }

  L.renderFolderTree = function () {
    return PR.sideTreeHtml(T.ordered(L.folderSync.folders), { catRow: row, tags: L.folderTags });
  };

  function select(folder) {
    L.tag = L.view === "folder" && L.tag === folder ? null : folder;
    L.view = L.tag ? "folder" : "all";
    showSide(false);
    L.render();
  }

  function chooseSource(item, folder, where, use) {
    const files = item.folder_files || [], direct = files.filter((f) => f.folder === folder);
    const from = direct.length ? direct : files;
    if (from.length < 2) return use(from[0] || null);
    PR.menu(where, from.map((f) => {
      const parts = T.parts(f.folder);
      if (!parts[0]) parts[0] = PR.t("全部");
      return { label: parts.concat(f.name).join(" › "), icon: "pdf", fn: () => use(f) };
    }));
  }

  function remember(item, mode, folder, where) {
    chooseSource(item, folder, where, (source) => {
      clipboard = { id: item.id, mode, source };
      PR.toast(PR.t("在目标文件夹上右键，选择“粘贴 PDF”"));
    });
  }

  L.folderMenuItems = function (item, where, folder) {
    if ((!L.folderSync.folders.length && !L.folderSync.default_enabled) || item.source_type === "web" || item.load_error) return [];
    const source = folder || (L.view === "folder" ? L.tag : null);
    return [
      { label: PR.t("复制 PDF"), icon: "copy", fn: () => remember(item, "copy", source, where) },
      ...(item.folder_files?.length ? [{ label: PR.t("剪切 PDF"), icon: "folder", fn: () => remember(item, "move", source, where) }] : []),
      ...(L.folderSync.windows && item.folder_files?.length ? [{ label: PR.t("复制到 Windows 剪贴板"), icon: "copy", fn: () =>
        chooseSource(item, source, where, async (file) => {
          try {
            await systemClipboard({ action: "copy", id: item.id, source: file });
            PR.toast(PR.t("已复制，可在资源管理器中粘贴"));
          } catch (e) { PR.toast(PR.esc(e.message)); }
        }) }] : []),
      "-",
    ];
  };

  const transfer = (target, entry) => PR.api("/api/folder-sync/transfer", { method: "POST", body: { ...entry, target } });
  const systemClipboard = (body) => PR.api("/api/folder-sync/clipboard", { method: "POST", body });

  async function paste(target) {
    if (!clipboard || pasting) return;
    const entry = clipboard;
    pasting = true;
    try {
      const result = await transfer(target, entry);
      if (result.moved && clipboard === entry) clipboard = null;
      PR.toast(result.warning ? PR.esc(result.warning) : result.moved ? PR.t("已移动 PDF") : PR.t("已复制 PDF"));
      await L.load();
    } catch (err) { PR.toast(PR.esc(err.message)); }
    finally { pasting = false; }
  }

  L.copyPdfsToFolder = async function (items, target) {
    for (const item of items) {
      if (item.folder_files?.some((file) => file.folder === target)) continue;
      try { await transfer(target, { id: item.id, mode: "copy" }); }
      catch (err) { PR.toast(PR.esc(err.message)); }
    }
    await L.load();
  };

  async function pasteSystem(target) {
    if (pasting) return;
    pasting = true;
    try {
      const result = await systemClipboard({ action: "paste", target });
      await L.load();
      const title = PR.t("已复制 {n} 个 PDF", { n: result.copied });
      if (result.failed.length) PR.showText(title, result.failed.join("\n"));
      else PR.toast(title);
    } catch (e) { PR.toast(PR.esc(e.message)); }
    finally { pasting = false; }
  }

  async function directory(body) {
    try {
      const result = await PR.api("/api/folder-sync/directory", { method: "POST", body });
      L.openTo("/" + T.parent(result.folder));
      if (L.view === "folder" && body.source && T.under(L.tag, body.source)) L.tag = T.move(L.tag, body.source, result.folder);
      L.saveSide();
      await L.load();
      if (result.warning) PR.toast(PR.esc(result.warning));
    } catch (e) { PR.toast(PR.esc(e.message)); }
  }

  async function nameFolder(folder, rename) {
    const name = await PR.promptText({ title: rename ? PR.t("重命名文件夹") : PR.t("新建子文件夹"),
      body: PR.t("此操作会更新磁盘上的文件夹。"), value: rename ? T.leaf(folder) : "", max: 255 });
    if (!name || (rename && name === T.leaf(folder))) return;
    await directory(rename ? { action: "move", source: folder, target: T.parent(folder), name } : { action: "create", target: folder, name });
  }

  const moveTargets = (folder) => L.folderSync.folders.filter((target) => target !== T.parent(folder) && !T.under(target, folder));
  function moveFolder(folder, where) {
    PR.menu(where, moveTargets(folder).map((target) => ({ label: T.label(target), icon: "folder", fn: async () => {
      const ok = await PR.confirm({ title: PR.t("移动文件夹"), body: PR.t("将磁盘上的“{from}”及其中的文件移到“{to}”。", { from: T.label(folder), to: T.label(target) }), ok: PR.t("移动") });
      if (ok) await directory({ action: "move", source: folder, target, name: T.leaf(folder) });
    } })));
  }

  L.folderPasteItems = (folder) => [
    { label: PR.t("粘贴 PDF"), icon: "page", disabled: !clipboard || pasting, fn: () => paste(folder) },
    ...(L.folderSync.windows ? [{ label: PR.t("粘贴 Windows 剪贴板中的 PDF"), icon: "page", disabled: pasting, fn: () => pasteSystem(folder) }] : []),
  ];

  function folderMenu(folder, where) {
    PR.menu(where, [
      ...L.folderPasteItems(folder),
      "-",
      { label: PR.t("新建子文件夹"), icon: "plus", fn: () => nameFolder(folder, false) },
      ...(L.folderSync.windows && T.parent(folder) ? [
        { label: PR.t("重命名文件夹"), icon: "edit", fn: () => nameFolder(folder, true) },
        { label: PR.t("移动文件夹"), icon: "folder", disabled: !moveTargets(folder).length, fn: () => moveFolder(folder, where) },
      ] : []),
    ]);
  }

  side.addEventListener("click", (e) => {
    const node = e.target.closest("[data-folder]");
    if (!node) return;
    const folder = node.dataset.folder, more = e.target.closest("[data-more]");
    if (more) return folderMenu(folder, more);
    if (e.target.closest("[data-fold]")) return L.toggleFold("/" + folder);
    select(folder);
  });
  side.addEventListener("contextmenu", (e) => {
    const node = e.target.closest("[data-folder]");
    if (!node) return;
    e.preventDefault();
    folderMenu(node.dataset.folder, { x: e.clientX, y: e.clientY });
  });
  side.addEventListener("keydown", (e) => {
    const node = e.target.closest("[data-folder]");
    if (node && e.key === "Enter") { e.preventDefault(); e.stopPropagation(); select(node.dataset.folder); }
  });
})(window.PR);
