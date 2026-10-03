/**
 * PDF-ChatGPT front-end application logic.
 *
 * Vanilla JavaScript (no build step) that talks to the FastAPI backend:
 *  - Uploads PDFs and polls the document library.
 *  - Streams chat answers via Server-Sent Events (fetch + ReadableStream).
 *  - Renders Markdown with syntax highlighting.
 *  - Persists the light/dark theme preference.
 */

(() => {
  "use strict";

  const API_BASE = "";
  const pdfjsLib = window.pdfjsLib;
  if (pdfjsLib) {
    pdfjsLib.GlobalWorkerOptions.workerSrc =
      "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js";
  }
  const state = {
    conversationId: null,
    documents: [],
    conversations: [],
    isStreaming: false,
    activeDocument: null,
    pdfDocument: null,
    pageObserver: null,
    visiblePageObserver: null,
    pageNodes: new Map(),
    renderedPages: new Set(),
    currentPage: 1,
    readerScale: 1,
    selectedText: null,
    searchQuery: "",
    searchNextPage: 1,
  };

  // ---------------------------------------------------------------------
  // DOM references
  // ---------------------------------------------------------------------
  const el = {
    sidebar: document.getElementById("sidebar"),
    sidebarToggle: document.getElementById("sidebar-toggle"),
    themeToggle: document.getElementById("theme-toggle"),
    newChatBtn: document.getElementById("new-chat-btn"),
    fileInput: document.getElementById("file-input"),
    uploadProgress: document.getElementById("upload-progress"),
    uploadProgressText: document.getElementById("upload-progress-text"),
    documentList: document.getElementById("document-list"),
    conversationList: document.getElementById("conversation-list"),
    documentScope: document.getElementById("document-scope"),
    chatMessages: document.getElementById("chat-messages"),
    chatForm: document.getElementById("chat-form"),
    chatInput: document.getElementById("chat-input"),
    sendBtn: document.getElementById("send-btn"),
    conversationTitle: document.getElementById("conversation-title"),
    healthIndicator: document.getElementById("health-indicator"),
    readerDocumentTitle: document.getElementById("reader-document-title"),
    readerPageTotal: document.getElementById("reader-page-total"),
    readerPrev: document.getElementById("reader-prev"),
    readerNext: document.getElementById("reader-next"),
    readerPageNumber: document.getElementById("reader-page-number"),
    readerZoomOut: document.getElementById("reader-zoom-out"),
    readerZoomIn: document.getElementById("reader-zoom-in"),
    readerZoomLevel: document.getElementById("reader-zoom-level"),
    readerSearchForm: document.getElementById("reader-search-form"),
    readerSearch: document.getElementById("reader-search"),
    readerSearchStatus: document.getElementById("reader-search-status"),
    pdfScroll: document.getElementById("pdf-scroll"),
    pdfPages: document.getElementById("pdf-pages"),
    readerEmpty: document.getElementById("reader-empty"),
    selectedTextPanel: document.getElementById("selected-text-panel"),
    selectedTextPage: document.getElementById("selected-text-page"),
    selectedTextContent: document.getElementById("selected-text-content"),
    clearSelection: document.getElementById("clear-selection"),
    explainSelection: document.getElementById("explain-selection"),
  };

  marked.setOptions({
    breaks: true,
    highlight: (code, lang) => {
      try {
        return lang && hljs.getLanguage(lang)
          ? hljs.highlight(code, { language: lang }).value
          : hljs.highlightAuto(code).value;
      } catch {
        return code;
      }
    },
  });

  // ---------------------------------------------------------------------
  // Theme
  // ---------------------------------------------------------------------
  function initTheme() {
    const saved = localStorage.getItem("pdf-chatgpt-theme") || "dark";
    document.documentElement.setAttribute("data-theme", saved);
  }

  el.themeToggle.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme");
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("pdf-chatgpt-theme", next);
  });

  el.sidebarToggle.addEventListener("click", () => {
    el.sidebar.classList.toggle("open");
  });

  // ---------------------------------------------------------------------
  // Toasts
  // ---------------------------------------------------------------------
  function toast(message, kind = "info") {
    let container = document.querySelector(".toast-container");
    if (!container) {
      container = document.createElement("div");
      container.className = "toast-container";
      document.body.appendChild(container);
    }
    const node = document.createElement("div");
    node.className = `toast ${kind}`;
    node.textContent = message;
    container.appendChild(node);
    setTimeout(() => node.remove(), 5000);
  }

  // ---------------------------------------------------------------------
  // Health check
  // ---------------------------------------------------------------------
  async function checkHealth() {
    try {
      const res = await fetch(`${API_BASE}/api/health`);
      const data = await res.json();
      el.healthIndicator.classList.add(data.openai_configured ? "ok" : "down");
      el.healthIndicator.title = data.openai_configured
        ? "API healthy - OpenAI key configured"
        : "API running, but OPENAI_API_KEY is missing";
    } catch {
      el.healthIndicator.classList.add("down");
      el.healthIndicator.title = "API unreachable";
    }
  }

  // ---------------------------------------------------------------------
  // Document library
  // ---------------------------------------------------------------------
  async function refreshDocuments() {
    try {
      const res = await fetch(`${API_BASE}/api/documents`);
      if (!res.ok) throw new Error("Failed to load documents");
      const data = await res.json();
      state.documents = data.documents;
      renderDocumentList();
      renderDocumentScope();
    } catch (err) {
      console.error(err);
    }
  }

  function renderDocumentList() {
    el.documentList.innerHTML = "";
    if (state.documents.length === 0) {
      el.documentList.innerHTML =
        '<li class="empty-state">No documents yet. Upload a PDF to get started.</li>';
      return;
    }
    for (const doc of state.documents) {
      const li = document.createElement("li");
      li.className = "document-item";
      li.innerHTML = `
        <div class="document-item-row">
          <button class="document-name document-open-btn" data-id="${doc.id}" title="Open PDF: ${escapeHtml(doc.filename)}">${escapeHtml(doc.filename)}</button>
          <button class="delete-doc-btn" data-id="${doc.id}" title="Delete document">
            <svg class="icon" viewBox="0 0 24 24" width="14" height="14"><path d="M3 6h18M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2m3 0-1 14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2L4 6"/></svg>
          </button>
        </div>
        <div class="document-meta">
          <span class="status-badge status-${doc.status}">${doc.status}</span>
          &middot; ${doc.page_count} pages &middot; ${doc.chunk_count} chunks
          ${doc.error_message ? `<div class="document-error" title="${escapeHtml(doc.error_message)}">${escapeHtml(doc.error_message)}</div>` : ""}
        </div>`;
      el.documentList.appendChild(li);
    }
    el.documentList.querySelectorAll(".delete-doc-btn").forEach((btn) => {
      btn.addEventListener("click", () => deleteDocument(btn.dataset.id));
    });
    el.documentList.querySelectorAll(".document-open-btn").forEach((btn) => {
      btn.addEventListener("click", () => openDocument(btn.dataset.id));
    });
  }

  function renderDocumentScope() {
    const previous = el.documentScope.value;
    el.documentScope.innerHTML = '<option value="">All documents</option>';
    for (const doc of state.documents) {
      if (doc.status !== "indexed") continue;
      const opt = document.createElement("option");
      opt.value = doc.id;
      opt.textContent = doc.filename;
      el.documentScope.appendChild(opt);
    }
    el.documentScope.value = previous || "";
  }

  async function deleteDocument(id) {
    if (!confirm("Delete this document? This cannot be undone.")) return;
    try {
      const res = await fetch(`${API_BASE}/api/documents/${id}`, {
        method: "DELETE",
      });
      if (!res.ok && res.status !== 204) throw new Error("Delete failed");
      toast("Document deleted.", "success");
      await refreshDocuments();
    } catch (err) {
      toast("Failed to delete document.", "error");
    }
  }

  async function uploadFiles(files) {
    let lastUploadedName = "";
    for (const file of files) {
      if (file.type !== "application/pdf" && !file.name.toLowerCase().endsWith(".pdf")) {
        toast(`${file.name} is not a PDF file.`, "error");
        continue;
      }
      lastUploadedName = file.name;
      el.uploadProgress.classList.remove("hidden");
      el.uploadProgressText.textContent = `Indexing ${file.name}...`;
      const formData = new FormData();
      formData.append("file", file);
      try {
        const res = await fetch(`${API_BASE}/api/documents`, {
          method: "POST",
          body: formData,
        });
        const data = await res.json();
        if (!res.ok) throw new Error(data.detail || "Upload failed");
        toast(`${file.name} indexed successfully.`, "success");
      } catch (err) {
        toast(`Failed to index ${file.name}: ${err.message}`, "error");
      }
    }
    el.uploadProgress.classList.add("hidden");
    await refreshDocuments();
    const uploadedDocument = state.documents.find((doc) => doc.filename === lastUploadedName);
    if (uploadedDocument) await openDocument(uploadedDocument.id);
  }

  el.fileInput.addEventListener("change", (e) => {
    if (e.target.files.length) uploadFiles(Array.from(e.target.files));
    e.target.value = "";
  });

  // ---------------------------------------------------------------------
  // PDF reader
  // ---------------------------------------------------------------------
  function setReaderControlsEnabled(enabled) {
    for (const control of [
      el.readerPrev,
      el.readerNext,
      el.readerPageNumber,
      el.readerZoomOut,
      el.readerZoomIn,
      el.readerSearch,
      el.readerSearchForm.querySelector("button"),
    ]) {
      control.disabled = !enabled;
    }
  }

  async function openDocument(documentId) {
    const selectedDocument = state.documents.find((item) => item.id === documentId);
    if (!selectedDocument) return;
    if (!pdfjsLib) {
      toast("The PDF viewer could not load. Check your network connection and reload.", "error");
      return;
    }

    state.activeDocument = selectedDocument;
    state.pdfDocument = null;
    state.renderedPages.clear();
    state.pageNodes.clear();
    state.currentPage = 1;
    state.readerScale = 1;
    clearSelection();
    state.pageObserver?.disconnect();
    state.visiblePageObserver?.disconnect();
    el.pdfPages.replaceChildren();
    el.readerEmpty.classList.add("hidden");
    el.readerDocumentTitle.textContent = selectedDocument.filename;
    el.readerPageTotal.textContent = "Loading PDF...";
    el.readerSearchStatus.textContent = "";
    setReaderControlsEnabled(false);

    try {
      const loadingTask = pdfjsLib.getDocument(`/api/documents/${documentId}/file`);
      const pdfDocument = await loadingTask.promise;
      state.pdfDocument = pdfDocument;
      el.readerPageTotal.textContent = `of ${pdfDocument.numPages}`;
      el.readerPageNumber.max = String(pdfDocument.numPages);
      el.readerPageNumber.value = "1";
      setReaderControlsEnabled(true);
      createPagePlaceholders(pdfDocument.numPages);
      el.pdfScroll.scrollTop = 0;
    } catch (error) {
      el.readerPageTotal.textContent = "Could not open PDF";
      el.readerEmpty.classList.remove("hidden");
      el.readerEmpty.querySelector("p").textContent =
        error.message || "The saved PDF could not be read.";
      toast("Could not open this PDF.", "error");
    }
  }

  function createPagePlaceholders(pageCount) {
    el.pdfPages.replaceChildren();
    for (let pageNumber = 1; pageNumber <= pageCount; pageNumber += 1) {
      const page = document.createElement("div");
      page.className = "pdf-page";
      page.dataset.pageNumber = String(pageNumber);
      page.setAttribute("aria-label", `PDF page ${pageNumber}`);
      const placeholder = document.createElement("div");
      placeholder.className = "pdf-page-placeholder";
      placeholder.textContent = `Page ${pageNumber}`;
      page.appendChild(placeholder);
      el.pdfPages.appendChild(page);
      state.pageNodes.set(pageNumber, page);
    }

    state.pageObserver = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            renderPdfPage(Number(entry.target.dataset.pageNumber));
            state.pageObserver.unobserve(entry.target);
          }
        }
      },
      { root: el.pdfScroll, rootMargin: "700px 0px", threshold: 0.01 },
    );
    state.visiblePageObserver = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort((a, b) => Math.abs(a.boundingClientRect.top) - Math.abs(b.boundingClientRect.top));
        if (visible.length) setCurrentPage(Number(visible[0].target.dataset.pageNumber));
      },
      { root: el.pdfScroll, threshold: 0.35 },
    );
    for (const page of state.pageNodes.values()) {
      state.pageObserver.observe(page);
      state.visiblePageObserver.observe(page);
    }
  }

  async function renderPdfPage(pageNumber) {
    const pdfDocument = state.pdfDocument;
    const wrapper = state.pageNodes.get(pageNumber);
    if (!pdfDocument || !wrapper || state.renderedPages.has(pageNumber)) return;
    state.renderedPages.add(pageNumber);
    try {
      const page = await pdfDocument.getPage(pageNumber);
      const baseViewport = page.getViewport({ scale: 1 });
      const availableWidth = Math.max(240, el.pdfScroll.clientWidth - 40);
      const scale = Math.min(state.readerScale, availableWidth / baseViewport.width);
      const viewport = page.getViewport({ scale });
      const outputScale = window.devicePixelRatio || 1;
      const canvas = document.createElement("canvas");
      canvas.className = "pdf-canvas";
      canvas.width = Math.ceil(viewport.width * outputScale);
      canvas.height = Math.ceil(viewport.height * outputScale);
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      const context = canvas.getContext("2d");
      context.setTransform(outputScale, 0, 0, outputScale, 0, 0);
      wrapper.replaceChildren(canvas);
      wrapper.style.width = `${viewport.width}px`;
      wrapper.style.aspectRatio = `${viewport.width} / ${viewport.height}`;

      await page.render({ canvasContext: context, viewport }).promise;
      const textLayer = document.createElement("div");
      textLayer.className = "pdf-text-layer textLayer";
      textLayer.style.width = `${viewport.width}px`;
      textLayer.style.height = `${viewport.height}px`;
      textLayer.style.setProperty("--scale-factor", String(viewport.scale));
      wrapper.appendChild(textLayer);
      const textContent = await page.getTextContent();
      await pdfjsLib.renderTextLayer({ textContent, container: textLayer, viewport, textDivs: [] }).promise;
      wrapper.classList.add("is-rendered");
      if (state.searchQuery) highlightSearchMatches(textLayer, state.searchQuery);
    } catch (error) {
      wrapper.textContent = `Page ${pageNumber} could not be rendered: ${error.message}`;
      wrapper.classList.add("pdf-page-error");
    }
  }

  function setCurrentPage(pageNumber) {
    state.currentPage = pageNumber;
    el.readerPageNumber.value = String(pageNumber);
  }

  function goToPage(pageNumber) {
    if (!state.pdfDocument) return;
    const safePage = Math.max(1, Math.min(state.pdfDocument.numPages, pageNumber));
    setCurrentPage(safePage);
    state.pageNodes.get(safePage)?.scrollIntoView({ behavior: "smooth", block: "start" });
    renderPdfPage(safePage);
  }

  function updateZoom(nextZoom) {
    if (!state.pdfDocument) return;
    state.readerScale = Math.max(0.65, Math.min(2, nextZoom));
    el.readerZoomLevel.textContent = `${Math.round(state.readerScale * 100)}%`;
    state.renderedPages.clear();
    createPagePlaceholders(state.pdfDocument.numPages);
    goToPage(state.currentPage);
  }

  async function findNextMatch() {
    const query = el.readerSearch.value.trim();
    const pdfDocument = state.pdfDocument;
    if (!query || !pdfDocument) return;
    const startPage = state.searchQuery === query ? state.searchNextPage : state.currentPage;
    state.searchQuery = query;
    el.readerSearchStatus.textContent = "Searching...";
    for (let offset = 0; offset < pdfDocument.numPages; offset += 1) {
      const pageNumber = ((startPage - 1 + offset) % pdfDocument.numPages) + 1;
      const page = await pdfDocument.getPage(pageNumber);
      const content = await page.getTextContent();
      const text = content.items.map((item) => item.str || "").join(" ");
      if (text.toLocaleLowerCase().includes(query.toLocaleLowerCase())) {
        state.searchNextPage = (pageNumber % pdfDocument.numPages) + 1;
        el.readerSearchStatus.textContent = `Match on page ${pageNumber}`;
        goToPage(pageNumber);
        const pageNode = state.pageNodes.get(pageNumber);
        await renderPdfPage(pageNumber);
        highlightSearchMatches(pageNode.querySelector(".pdf-text-layer"), query);
        return;
      }
    }
    el.readerSearchStatus.textContent = "No matches found";
  }

  function highlightSearchMatches(textLayer, query) {
    if (!textLayer) return;
    textLayer.querySelectorAll(".pdf-search-hit").forEach((span) => span.classList.remove("pdf-search-hit"));
    const normalizedQuery = query.toLocaleLowerCase();
    for (const span of textLayer.querySelectorAll("span")) {
      if (span.textContent.toLocaleLowerCase().includes(normalizedQuery)) {
        span.classList.add("pdf-search-hit");
      }
    }
  }

  function showSelection(text, pageNumber) {
    state.selectedText = {
      text: text.slice(0, 12000),
      documentId: state.activeDocument.id,
      pageNumber,
    };
    el.selectedTextPage.textContent = `Selected text · page ${pageNumber}`;
    el.selectedTextContent.textContent = state.selectedText.text;
    el.selectedTextPanel.classList.remove("hidden");
  }

  function clearSelection() {
    state.selectedText = null;
    el.selectedTextPanel.classList.add("hidden");
    el.selectedTextContent.textContent = "";
    window.getSelection()?.removeAllRanges();
  }

  document.addEventListener("selectionchange", () => {
    if (!state.activeDocument) return;
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (!text) return;
    const anchor = selection.anchorNode instanceof Element
      ? selection.anchorNode
      : selection.anchorNode?.parentElement;
    const page = anchor?.closest(".pdf-page");
    if (page && page.closest("#pdf-pages")) showSelection(text, Number(page.dataset.pageNumber));
  });

  el.readerPrev.addEventListener("click", () => goToPage(state.currentPage - 1));
  el.readerNext.addEventListener("click", () => goToPage(state.currentPage + 1));
  el.readerPageNumber.addEventListener("change", () => goToPage(Number(el.readerPageNumber.value)));
  el.readerZoomOut.addEventListener("click", () => updateZoom(state.readerScale - 0.15));
  el.readerZoomIn.addEventListener("click", () => updateZoom(state.readerScale + 0.15));
  el.readerSearchForm.addEventListener("submit", (event) => {
    event.preventDefault();
    findNextMatch();
  });
  el.clearSelection.addEventListener("click", clearSelection);
  el.explainSelection.addEventListener("click", () => {
    el.chatInput.value = "Explain this in simple words.";
    el.chatInput.focus();
    el.chatInput.dispatchEvent(new Event("input"));
  });

  // ---------------------------------------------------------------------
  // Conversations
  // ---------------------------------------------------------------------
  async function refreshConversations() {
    try {
      const res = await fetch(`${API_BASE}/api/conversations`);
      const data = await res.json();
      state.conversations = data.conversations;
      renderConversationList();
    } catch (err) {
      console.error(err);
    }
  }

  function renderConversationList() {
    el.conversationList.innerHTML = "";
    if (state.conversations.length === 0) {
      el.conversationList.innerHTML =
        '<li class="empty-state">No conversations yet.</li>';
      return;
    }
    for (const conv of state.conversations) {
      const li = document.createElement("li");
      li.className =
        "conversation-item" +
        (conv.id === state.conversationId ? " active" : "");
      li.textContent = conv.title || "Untitled conversation";
      li.addEventListener("click", () => loadConversation(conv.id));
      el.conversationList.appendChild(li);
    }
  }

  async function loadConversation(id) {
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${id}`);
      if (!res.ok) throw new Error("Conversation not found");
      const data = await res.json();
      state.conversationId = data.id;
      el.conversationTitle.textContent = data.title || "Conversation";
      el.chatMessages.innerHTML = "";
      for (const msg of data.messages) {
        appendMessage(msg.role, msg.content, msg.sources || []);
      }
      renderConversationList();
    } catch (err) {
      toast("Failed to load conversation.", "error");
    }
  }

  el.newChatBtn.addEventListener("click", () => {
    state.conversationId = null;
    el.conversationTitle.textContent = "New Conversation";
    el.chatMessages.innerHTML = `
      <div class="welcome-card">
        <h2>Welcome to PDF-ChatGPT</h2>
        <p>Upload one or more PDF documents, then ask questions in natural language. Answers are generated
           using Retrieval-Augmented Generation (RAG) and always cite the source document and page.</p>
      </div>`;
    renderConversationList();
  });

  // ---------------------------------------------------------------------
  // Chat / streaming
  // ---------------------------------------------------------------------
  function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str;
    return div.innerHTML;
  }

  function appendMessage(role, content, sources = []) {
    const welcome = el.chatMessages.querySelector(".welcome-card");
    if (welcome) welcome.remove();

    const row = document.createElement("div");
    row.className = `message-row ${role}`;

    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = role === "user" ? "You" : "AI";

    const bubble = document.createElement("div");
    bubble.className = "bubble";
    bubble.innerHTML = marked.parse(content || "");

    if (sources.length) {
      const block = document.createElement("div");
      block.className = "sources-block";
      block.innerHTML = sources
        .map(
          (s) =>
            `<div class="source-chip"><b>${escapeHtml(s.document_name)}</b> - page ${s.page_number} (relevance ${(s.relevance_score * 100).toFixed(0)}%)</div>`,
        )
        .join("");
      bubble.appendChild(block);
    }

    row.appendChild(avatar);
    row.appendChild(bubble);
    el.chatMessages.appendChild(row);
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
    return bubble;
  }

  function appendUserMessage(question, selectedText) {
    const welcome = el.chatMessages.querySelector(".welcome-card");
    if (welcome) welcome.remove();

    const row = document.createElement("div");
    row.className = "message-row user";
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "You";
    const bubble = document.createElement("div");
    bubble.className = "bubble";
    if (selectedText) {
      const quote = document.createElement("blockquote");
      quote.className = "selected-quote";
      quote.textContent = selectedText;
      bubble.appendChild(quote);
    }
    const questionText = document.createElement("div");
    questionText.textContent = question;
    bubble.appendChild(questionText);
    row.append(avatar, bubble);
    el.chatMessages.appendChild(row);
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  }

  function appendTypingIndicator() {
    const row = document.createElement("div");
    row.className = "message-row assistant";
    row.id = "typing-row";
    row.innerHTML = `
      <div class="avatar">AI</div>
      <div class="bubble"><span class="typing-indicator"><span></span><span></span><span></span></span></div>`;
    el.chatMessages.appendChild(row);
    el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
  }

  function removeTypingIndicator() {
    document.getElementById("typing-row")?.remove();
  }

  async function sendMessage(question) {
    if (state.isStreaming) return;
    state.isStreaming = true;
    el.sendBtn.disabled = true;

    const selected = state.selectedText;
    appendUserMessage(question, selected?.text);
    appendTypingIndicator();

    const explanationPayload = selected
      ? {
          selected_text: selected.text,
          user_question: question,
          document_id: selected.documentId,
          page_number: selected.pageNumber,
          conversation_id: state.conversationId,
        }
      : null;
    const payload = explanationPayload || {
      question,
      conversation_id: state.conversationId,
      document_ids: el.documentScope.value ? [el.documentScope.value] : null,
    };

    try {
      const endpoint = explanationPayload ? "/api/chat/explain/stream" : "/api/chat/stream";
      const res = await fetch(`${API_BASE}${endpoint}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });

      if (!res.ok || !res.body) {
        const errData = await res.json().catch(() => ({}));
        throw new Error(errData.detail || `Request failed (${res.status})`);
      }

      removeTypingIndicator();
      const bubble = appendMessage("assistant", "");
      let buffer = "";
      let answerText = "";

      const reader = res.body.getReader();
      const decoder = new TextDecoder();

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });

        const events = buffer.split("\n\n");
        buffer = events.pop() ?? "";

        for (const rawEvent of events) {
          const { event, data } = parseSSE(rawEvent);
          if (!event) continue;
          if (event === "meta") {
            state.conversationId = data.conversation_id;
          } else if (event === "token") {
            answerText += data.text;
            bubble.innerHTML = marked.parse(answerText);
            el.chatMessages.scrollTop = el.chatMessages.scrollHeight;
          } else if (event === "sources" && data.sources?.length) {
            const block = document.createElement("div");
            block.className = "sources-block";
            block.innerHTML = data.sources
              .map(
                (s) =>
                  `<div class="source-chip"><b>${escapeHtml(s.document_name)}</b> - page ${s.page_number} (relevance ${(s.relevance_score * 100).toFixed(0)}%)</div>`,
              )
              .join("");
            bubble.appendChild(block);
          } else if (event === "error") {
            throw new Error(data.detail || "Streaming error");
          }
        }
      }

      hljs.highlightAll();
      await refreshConversations();
      el.conversationTitle.textContent =
        state.conversations.find((c) => c.id === state.conversationId)?.title ||
        "Conversation";
    } catch (err) {
      removeTypingIndicator();
      appendMessage("assistant", `⚠️ **Error:** ${err.message}`);
      toast(err.message, "error");
    } finally {
      state.isStreaming = false;
      el.sendBtn.disabled = false;
    }
  }

  function parseSSE(rawEvent) {
    const lines = rawEvent.split("\n");
    let event = null;
    let dataStr = "";
    for (const line of lines) {
      if (line.startsWith("event:")) event = line.slice(6).trim();
      if (line.startsWith("data:")) dataStr += line.slice(5).trim();
    }
    let data = {};
    try {
      data = dataStr ? JSON.parse(dataStr) : {};
    } catch {
      data = {};
    }
    return { event, data };
  }

  el.chatForm.addEventListener("submit", (e) => {
    e.preventDefault();
    const question = el.chatInput.value.trim();
    if (!question) return;
    el.chatInput.value = "";
    el.chatInput.style.height = "auto";
    sendMessage(question);
  });

  el.chatInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      el.chatForm.requestSubmit();
    }
  });

  el.chatInput.addEventListener("input", () => {
    el.chatInput.style.height = "auto";
    el.chatInput.style.height = `${Math.min(el.chatInput.scrollHeight, 160)}px`;
  });

  // ---------------------------------------------------------------------
  // Init
  // ---------------------------------------------------------------------
  initTheme();
  checkHealth();
  refreshDocuments();
  refreshConversations();
})();
