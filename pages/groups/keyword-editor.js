// Shared by the global and per-group keyword forms.
export function createKeywordEditor(root, rule, apiPost, isNew = false) {
  root.classList.add("keyword-rich-editor");
  root.innerHTML = '<label><span>触发方式</span><select class="reply-trigger"><option value="any">所有消息（沿用原规则）</option><option value="command">仅 @机器人或唤醒指令</option><option value="auto">仅普通群消息</option></select></label>' +
    '<label><span>图文排列</span><select class="reply-send-mode"><option value="text_first">先发文字，再发图片</option><option value="combined">合并发送（由 QQ 排列）</option></select></label>' +
    '<p class="hint">先文字后图片：一张图片配文字会分成两条消息；多张图片依次发送。仅文字或仅图片不增加空消息。</p>' +
    '<label class="checkbox"><input class="reply-case" type="checkbox"><span>区分英文大小写</span></label>' +
    '<label class="checkbox"><input class="reply-menu" type="checkbox"><span>在群菜单中显示（所有群成员可点击）</span></label>' +
    '<p class="hint">群内 @机器人发送 #群菜单；按钮使用规则名称（1–10 字），正则规则暂不支持。</p>' +
    '<p class="hint">每次随机选一套回复；每套可填文字、图片或图文，最多 3 张图片。正则第一组可用 {1} 引用。仅指令模式匹配唤醒后的第一个词。</p>' +
    '<div class="reply-options"></div><button class="text-button reply-add" type="button">添加一套随机回复</button>' +
    '<p class="reply-error" role="alert"></p>';
  const trigger = root.querySelector(".reply-trigger");
  trigger.value = rule.trigger || "any";
  const sendMode = root.querySelector(".reply-send-mode");
  sendMode.value = rule.send_mode || (isNew ? "text_first" : "combined");
  const caseSensitive = root.querySelector(".reply-case");
  caseSensitive.checked = rule.case_sensitive === true;
  const showInMenu = root.querySelector(".reply-menu");
  showInMenu.checked = rule.show_in_menu === true;
  const list = root.querySelector(".reply-options");
  const error = root.querySelector(".reply-error");
  const addButton = root.querySelector(".reply-add");
  let pending = 0;
  const options = [];
  const details = root.closest("details");
  if (details) details.addEventListener("toggle", () => {
    options.forEach(option => option.images.forEach(image => { void image.preview(); }));
  });

  function showError(exc) { error.textContent = exc.message || String(exc); }
  function syncButtons() {
    addButton.disabled = options.length >= 20;
    options.forEach(option => { option.remove.disabled = options.length === 1 || option.busy; });
  }
  function fileData(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error("无法读取图片，请重新选择"));
      reader.onabort = () => reject(new Error("图片读取已取消"));
      reader.readAsDataURL(file);
    });
  }
  function addOption(value) {
    if (options.length >= 20) return;
    const field = document.createElement("fieldset");
    field.className = "reply-option";
    field.innerHTML = '<legend>一套回复</legend><label><span>回复文字（有图片时可留空）</span><textarea class="reply-text" maxlength="1000" rows="3"></textarea></label>' +
      '<div class="reply-images"></div><label><span>添加图片（单张 ≤ 8 MB，PNG / JPEG / GIF / WebP）</span><input class="reply-upload" type="file" accept="image/png,image/jpeg,image/gif,image/webp" multiple></label>' +
      '<span class="reply-status" role="status"></span><button type="button" class="text-button danger reply-remove">删除这套回复</button>';
    list.appendChild(field);
    const option = {field, images: [], busy: false, remove: field.querySelector(".reply-remove")};
    options.push(option);
    const text = field.querySelector(".reply-text");
    text.value = value.text || "";
    option.text = text;
    const upload = field.querySelector(".reply-upload");
    const status = field.querySelector(".reply-status");
    const images = field.querySelector(".reply-images");
    function appendImage(id) {
      const figure = document.createElement("figure");
      const img = document.createElement("img");
      img.alt = "关键词回复图片";
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "text-button danger";
      remove.textContent = "移除图片";
      const item = {id, figure, loaded: false};
      option.images.push(item);
      remove.addEventListener("click", () => {
        option.images.splice(option.images.indexOf(item), 1);
        figure.remove();
      });
      figure.append(img, remove);
      images.appendChild(figure);
      async function preview() {
        if (item.loaded || (details && !details.open) || !option.images.includes(item)) return;
        item.loaded = true;
        try {
          const result = await apiPost("keyword-images/preview", {id});
          img.src = result.data_url;
        } catch (exc) {
          img.alt = "图片预览失败，请移除后重新上传";
          showError(exc);
        }
      }
      item.preview = preview;
      void preview();
    }
    (value.images || []).forEach(id => appendImage(id));
    upload.addEventListener("change", async () => {
      const files = Array.from(upload.files || []);
      if (!files.length) return;
      error.textContent = "";
      option.busy = true;
      pending++;
      upload.disabled = true;
      syncButtons();
      try {
        if (option.images.length + files.length > 3) throw new Error("每套回复最多 3 张图片");
        if (files.some(file => !file.size || file.size > 8 * 1024 * 1024)) throw new Error("图片不能为空或超过 8 MB");
        for (const file of files) {
          status.textContent = "正在上传：" + file.name;
          const dataUrl = await fileData(file);
          const result = await apiPost("keyword-images/upload", {base64: dataUrl.slice(dataUrl.indexOf(",") + 1)});
          appendImage(result.id);
        }
        status.textContent = "图片已上传，保存规则后生效";
      } catch (exc) {
        status.textContent = "上传未完成；已成功上传的图片保留在列表中";
        showError(exc);
      } finally {
        pending--;
        option.busy = false;
        upload.disabled = false;
        upload.value = "";
        syncButtons();
      }
    });
    option.remove.addEventListener("click", () => {
      if (option.busy || options.length === 1) return;
      options.splice(options.indexOf(option), 1);
      field.remove();
      syncButtons();
    });
    syncButtons();
  }
  addButton.addEventListener("click", () => addOption({text: "", images: []}));
  (Array.isArray(rule.responses) && rule.responses.length ? rule.responses : [{text: rule.reply || "", images: []}]).forEach(addOption);
  return {
    read() {
      error.textContent = "";
      try {
        if (pending) throw new Error("图片正在上传，请完成后再保存");
        if (showInMenu.checked && details && details.querySelector(".global-rule-mode, .keyword-reply-mode")?.value === "regex") throw new Error("正则规则暂不支持群菜单");
        const responses = options.map(option => ({text: option.text.value.trim(), images: option.images.map(image => image.id)}));
        if (responses.some(response => !response.text && !response.images.length)) throw new Error("每套回复至少填写文字或添加一张图片");
        const result = {reply: responses[0].text, trigger: trigger.value, case_sensitive: caseSensitive.checked, send_mode: sendMode.value, show_in_menu: showInMenu.checked};
        if (responses.length > 1 || responses[0].images.length) result.responses = responses;
        return result;
      } catch (exc) {
        showError(exc);
        if (details) details.open = true;
        throw exc;
      }
    }
  };
}
