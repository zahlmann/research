// PDF.js setup
const pdfjsLib = await import("https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.min.mjs");
pdfjsLib.GlobalWorkerOptions.workerSrc = "https://cdnjs.cloudflare.com/ajax/libs/pdf.js/4.4.168/pdf.worker.min.mjs";

// State
let currentSlug = null;
let pdfDoc = null;
let currentPage = 1;
let scale = 1.3;
let selectedText = "";
let selectedPage = null;
let statusPollInterval = null;
let isAsking = false;
let renderGeneration = 0; // guard against concurrent renders

// Elements
const fileInput = document.getElementById("file-input");
const pdfSelect = document.getElementById("pdf-select");
const statusIndicator = document.getElementById("status-indicator");
const pdfTitle = document.getElementById("pdf-title");
const zoomOut = document.getElementById("zoom-out");
const zoomIn = document.getElementById("zoom-in");
const zoomLevel = document.getElementById("zoom-level");
const dropZone = document.getElementById("drop-zone");
const dropMessage = document.getElementById("drop-message");
const pdfContainer = document.getElementById("pdf-container");
const prevPage = document.getElementById("prev-page");
const nextPage = document.getElementById("next-page");
const pageInfo = document.getElementById("page-info");
const selectionText = document.getElementById("selection-text");
const questionInput = document.getElementById("question-input");
const askBtn = document.getElementById("ask-btn");
const chatHistory = document.getElementById("chat-history");
const clearBtn = document.getElementById("clear-btn");

// --- LaTeX rendering ---

function renderMath(el) {
    if (window.renderMathInElement) {
        renderMathInElement(el, {
            delimiters: [
                { left: "$$", right: "$$", display: true },
                { left: "$", right: "$", display: false },
                { left: "\\(", right: "\\)", display: false },
                { left: "\\[", right: "\\]", display: true },
            ],
            throwOnError: false,
        });
    }
}

// --- PDF List ---

async function loadPdfList() {
    const resp = await fetch("/api/pdfs");
    const pdfs = await resp.json();
    pdfSelect.innerHTML = '<option value="">Select a PDF...</option>';
    for (const pdf of pdfs) {
        const opt = document.createElement("option");
        opt.value = pdf.slug;
        opt.textContent = `${pdf.title || pdf.slug} (${pdf.status})`;
        pdfSelect.appendChild(opt);
    }
}

pdfSelect.addEventListener("change", () => {
    if (pdfSelect.value) openPdf(pdfSelect.value);
});

// --- Upload ---

fileInput.addEventListener("change", async () => {
    if (fileInput.files.length === 0) return;
    await uploadFile(fileInput.files[0]);
    fileInput.value = "";
});

// Drag and drop
dropZone.addEventListener("dragover", (e) => {
    e.preventDefault();
    dropZone.classList.add("dragover");
});
dropZone.addEventListener("dragleave", () => {
    dropZone.classList.remove("dragover");
});
dropZone.addEventListener("drop", async (e) => {
    e.preventDefault();
    dropZone.classList.remove("dragover");
    const files = e.dataTransfer.files;
    if (files.length > 0 && files[0].name.toLowerCase().endsWith(".pdf")) {
        await uploadFile(files[0]);
    }
});

async function uploadFile(file) {
    const formData = new FormData();
    formData.append("file", file);
    statusIndicator.textContent = "Uploading...";
    statusIndicator.className = "active";

    const resp = await fetch("/api/upload", { method: "POST", body: formData });
    const data = await resp.json();
    if (data.slug) {
        await loadPdfList();
        openPdf(data.slug);
    }
}

// --- Open PDF ---

async function openPdf(slug) {
    currentSlug = slug;
    pdfSelect.value = slug;
    chatHistory.innerHTML = "";
    selectedText = "";
    selectionText.textContent = "(select text in the PDF)";
    selectionText.classList.remove("has-text");

    // Start status polling
    startStatusPoll(slug);

    // Load PDF
    const loadingTask = pdfjsLib.getDocument(`/api/pdfs/${slug}/pdf`);
    pdfDoc = await loadingTask.promise;
    currentPage = 1;
    dropMessage.classList.add("hidden");
    renderAllPages();
    updatePageNav();
}

// --- Status Polling ---

function startStatusPoll(slug) {
    if (statusPollInterval) clearInterval(statusPollInterval);
    pollStatus(slug);
    statusPollInterval = setInterval(() => pollStatus(slug), 2000);
}

async function pollStatus(slug) {
    const resp = await fetch(`/api/pdfs/${slug}/status`);
    const meta = await resp.json();
    pdfTitle.textContent = meta.title || slug;

    const statusMap = {
        queued: "Queued...",
        extracting: "Extracting text...",
        describing_images: "Describing images...",
        chunking: "Chunking...",
        embedding: "Embedding...",
        ready: "Ready",
    };

    statusIndicator.textContent = statusMap[meta.status] || meta.status;
    if (meta.status === "ready") {
        statusIndicator.className = "ready";
        if (meta.chunks !== undefined) {
            statusIndicator.textContent = `Ready (${meta.chunks} chunks, ${meta.images || 0} images)`;
        }
        if (statusPollInterval) {
            clearInterval(statusPollInterval);
            statusPollInterval = null;
        }
        await loadPdfList();
    } else {
        statusIndicator.className = "active";
    }
}

// --- PDF Rendering ---

async function renderAllPages() {
    const thisGeneration = ++renderGeneration;
    pdfContainer.innerHTML = "";

    for (let i = 1; i <= pdfDoc.numPages; i++) {
        // Abort if a newer render was started (e.g. user clicked zoom again)
        if (renderGeneration !== thisGeneration) return;

        const page = await pdfDoc.getPage(i);
        if (renderGeneration !== thisGeneration) return;

        const viewport = page.getViewport({ scale });

        const wrapper = document.createElement("div");
        wrapper.className = "page-wrapper";
        wrapper.dataset.page = i;
        wrapper.style.width = viewport.width + "px";
        wrapper.style.height = viewport.height + "px";

        // Canvas
        const canvas = document.createElement("canvas");
        canvas.width = viewport.width;
        canvas.height = viewport.height;
        const ctx = canvas.getContext("2d");
        await page.render({ canvasContext: ctx, viewport }).promise;
        if (renderGeneration !== thisGeneration) return;

        wrapper.appendChild(canvas);

        // Text layer
        const textLayerDiv = document.createElement("div");
        textLayerDiv.className = "textLayer";
        textLayerDiv.style.width = viewport.width + "px";
        textLayerDiv.style.height = viewport.height + "px";

        const textContent = await page.getTextContent();
        for (const item of textContent.items) {
            if (!item.str) continue;
            const tx = pdfjsLib.Util.transform(viewport.transform, item.transform);
            const span = document.createElement("span");
            span.textContent = item.str;
            span.style.left = tx[4] + "px";
            span.style.top = (tx[5] - item.height) + "px";
            span.style.fontSize = Math.abs(tx[3]) + "px";
            span.style.fontFamily = item.fontName || "sans-serif";
            const spanWidth = item.width * viewport.scale;
            if (spanWidth > 0) {
                span.style.width = spanWidth + "px";
                span.style.display = "inline-block";
            }
            textLayerDiv.appendChild(span);
        }

        wrapper.appendChild(textLayerDiv);
        pdfContainer.appendChild(wrapper);
    }
    updatePageNav();
}

function updatePageNav() {
    if (!pdfDoc) {
        pageInfo.textContent = "Page 0 of 0";
        prevPage.disabled = true;
        nextPage.disabled = true;
        return;
    }
    pageInfo.textContent = `Page ${currentPage} of ${pdfDoc.numPages}`;
    prevPage.disabled = currentPage <= 1;
    nextPage.disabled = currentPage >= pdfDoc.numPages;
}

prevPage.addEventListener("click", () => {
    if (currentPage > 1) {
        currentPage--;
        scrollToPage(currentPage);
        updatePageNav();
    }
});

nextPage.addEventListener("click", () => {
    if (pdfDoc && currentPage < pdfDoc.numPages) {
        currentPage++;
        scrollToPage(currentPage);
        updatePageNav();
    }
});

function scrollToPage(pageNum) {
    const wrapper = pdfContainer.querySelector(`.page-wrapper[data-page="${pageNum}"]`);
    if (wrapper) {
        wrapper.scrollIntoView({ behavior: "smooth", block: "start" });
    }
}

// Track current page on scroll
dropZone.addEventListener("scroll", () => {
    const wrappers = pdfContainer.querySelectorAll(".page-wrapper");
    const scrollTop = dropZone.scrollTop + 100;
    for (const w of wrappers) {
        if (w.offsetTop + w.offsetHeight > scrollTop) {
            const pg = parseInt(w.dataset.page);
            if (pg !== currentPage) {
                currentPage = pg;
                updatePageNav();
            }
            break;
        }
    }
});

// --- Zoom ---

zoomOut.addEventListener("click", () => {
    if (scale > 0.5) {
        scale -= 0.2;
        zoomLevel.textContent = Math.round(scale / 1.3 * 100) + "%";
        if (pdfDoc) renderAllPages();
    }
});

zoomIn.addEventListener("click", () => {
    if (scale < 4) {
        scale += 0.2;
        zoomLevel.textContent = Math.round(scale / 1.3 * 100) + "%";
        if (pdfDoc) renderAllPages();
    }
});

// --- Text Selection ---

document.addEventListener("mouseup", () => {
    const selection = window.getSelection();
    const text = selection?.toString().trim();
    if (text && text.length > 0) {
        selectedText = text;
        selectionText.textContent = text.length > 300 ? text.slice(0, 300) + "..." : text;
        selectionText.classList.add("has-text");

        // Try to determine which page the selection is on
        const anchorNode = selection.anchorNode;
        if (anchorNode) {
            const wrapper = anchorNode.parentElement?.closest(".page-wrapper");
            if (wrapper) {
                selectedPage = parseInt(wrapper.dataset.page);
            }
        }
    }
});

// --- Clear chat ---

clearBtn.addEventListener("click", () => {
    chatHistory.innerHTML = "";
});

// --- Q&A ---

askBtn.addEventListener("click", askQuestion);
questionInput.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        askQuestion();
    }
});

async function askQuestion() {
    const question = questionInput.value.trim();
    if (!question || !currentSlug || isAsking) return;

    isAsking = true;
    askBtn.disabled = true;
    questionInput.value = "";

    // Create Q&A pair in chat
    const pair = document.createElement("div");
    pair.className = "qa-pair";

    const qDiv = document.createElement("div");
    qDiv.className = "qa-question";
    qDiv.textContent = question;
    pair.appendChild(qDiv);

    if (selectedText) {
        const ctxDiv = document.createElement("div");
        ctxDiv.className = "qa-context";
        ctxDiv.textContent = selectedText.length > 200
            ? selectedText.slice(0, 200) + "..."
            : selectedText;
        pair.appendChild(ctxDiv);
    }

    const aDiv = document.createElement("div");
    aDiv.className = "qa-answer";
    aDiv.innerHTML = '<span class="thinking-dots"><span></span><span></span><span></span></span>';
    pair.appendChild(aDiv);

    chatHistory.appendChild(pair);
    chatHistory.scrollTop = chatHistory.scrollHeight;

    // Stream answer via SSE
    let fullText = "";
    try {
        const resp = await fetch(`/api/pdfs/${currentSlug}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                question,
                selected_text: selectedText,
                page: selectedPage,
            }),
        });

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = "";
        while (true) {
            const { done, value } = await reader.read();
            if (done) break;

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split("\n");
            buffer = lines.pop();

            for (const line of lines) {
                if (!line.startsWith("data: ")) continue;
                const data = line.slice(6);
                if (data === "[DONE]") continue;

                try {
                    const parsed = JSON.parse(data);
                    if (parsed.error) {
                        fullText += `\n\n**Error:** ${parsed.error}`;
                    } else if (parsed.text) {
                        if (parsed.final && !fullText) {
                            fullText = parsed.text;
                        } else if (!parsed.final) {
                            if (fullText) fullText += "\n\n";
                            fullText += parsed.text;
                        }
                    }
                } catch {}
            }

            aDiv.innerHTML = marked.parse(fullText) + '<span class="thinking-dots"><span></span><span></span><span></span></span>';
            renderMath(aDiv);
            chatHistory.scrollTop = chatHistory.scrollHeight;
        }

        // Final render
        aDiv.innerHTML = marked.parse(fullText || "(No response)");
        renderMath(aDiv);
    } catch (err) {
        aDiv.innerHTML = `<p style="color: #ef5350;">Error: ${err.message}</p>`;
    }

    isAsking = false;
    askBtn.disabled = false;
    chatHistory.scrollTop = chatHistory.scrollHeight;
}

// --- Init ---

loadPdfList();
