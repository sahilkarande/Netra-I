// ===============================================================
// VIEW EXAM JS - FINAL COMPLETE VERSION
// ===============================================================

// -------------------------
// Extend Time Modal
// -------------------------
function showExtendTimeModal() {
    document.getElementById("extendTimeModal").classList.add("active");
}
function hideExtendTimeModal() {
    document.getElementById("extendTimeModal").classList.remove("active");
}

setInterval(async () => {
    const p = new URLSearchParams(window.location.search);
    if (p.has("no_refresh")) return;

    const r = await fetch(window.location.href);
    const html = await r.text();

    // choose the part you want to update
    const newDoc = new DOMParser().parseFromString(html, "text/html");
    document.querySelector("#content").innerHTML =
        newDoc.querySelector("#content").innerHTML;

}, 1000);




// ===============================================================
// MAIN MODAL LOGIC (Access Modal + Student Selection Modal)
// ===============================================================
document.addEventListener("DOMContentLoaded", () => {

    // -------------------------
    // ELEMENTS
    // -------------------------
    const accessModal = document.getElementById("exam-access-modal");
    const accessOpenBtns = document.querySelectorAll(".open-access-modal");
    const accessCloseBtns = document.querySelectorAll(".close-access-modal");

    const allowedInput = document.getElementById("allowed-students-input");

    const previewBox = document.getElementById("selected-students-preview");
    const previewCount = document.getElementById("selected-count");
    const previewList = document.getElementById("selected-students-list");

    const radioStopped = document.querySelector("input[value='stopped']");
    const radioAll = document.querySelector("input[value='all']");
    const radioSpecific = document.querySelector("input[value='specific']");
    const studentSelectorButton = document.getElementById("open-student-selection");

    // -------------------------
    // ACCESS MODAL OPEN/CLOSE
    // -------------------------
    accessOpenBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            accessModal.classList.add("active");
            updateSelectedPreview();
        });
    });

    accessCloseBtns.forEach(btn => {
        btn.addEventListener("click", () => {
            accessModal.classList.remove("active");
        });
    });

    // Close on background click
    accessModal.addEventListener("click", e => {
        if (e.target === accessModal) {
            accessModal.classList.remove("active");
        }
    });


    // ===============================================================
    // SELECTED STUDENTS PREVIEW (INSIDE ACCESS CONTROL MODAL)
    // ===============================================================
    function updateSelectedPreview() {
        const ids = (allowedInput.value || "")
            .split(",")
            .map(x => x.trim())
            .filter(x => x !== "");

        if (ids.length === 0 || !radioSpecific.checked) {
            previewBox.style.display = "none";
            return;
        }

        previewBox.style.display = "block";
        previewCount.textContent = ids.length;
        previewList.innerHTML = "";

        ids.forEach(id => {
            const row = document.querySelector(`.student-row[data-student-id="${id}"]`);
            if (!row) return;

            const name = row.querySelector(".student-name")?.textContent || `ID ${id}`;
            const roll = row.dataset.rollId || "N/A";

            const li = document.createElement("li");
            li.textContent = `${name} (Roll ${roll})`;
            previewList.appendChild(li);
        });
    }


    // ===============================================================
    // ACCESS MODE LOGIC
    // ===============================================================
    function updateAccessMode() {
        if (radioSpecific.checked) {
            studentSelectorButton.style.display = "inline-block";
            updateSelectedPreview();
        } else {
            studentSelectorButton.style.display = "none";
            previewBox.style.display = "none";
        }
    }

    [radioStopped, radioAll, radioSpecific].forEach(radio => {
        if (radio) radio.addEventListener("change", updateAccessMode);
    });

    updateAccessMode(); // run on load


    // ===============================================================
    // STUDENT SELECTION MODAL (OPEN FROM ACCESS MODAL)
    // ===============================================================
    const studentModal = document.getElementById("student-selection-modal");
    const studentModalClose = document.getElementById("modal-close-btn");
    const studentModalCancel = document.getElementById("modal-cancel-btn");
    const saveStudentsBtn = document.getElementById("modal-save-btn");

    studentSelectorButton?.addEventListener("click", () => {
        accessModal.classList.remove("active");
        studentModal.classList.add("active");
    });

    function closeStudentModal() {
        studentModal.classList.remove("active");
    }

    studentModalClose?.addEventListener("click", closeStudentModal);
    studentModalCancel?.addEventListener("click", closeStudentModal);

    studentModal.addEventListener("click", e => {
        if (e.target === studentModal) closeStudentModal();
    });


    // ===============================================================
    // STUDENT LIST LOGIC
    // ===============================================================
    const studentSearch = document.getElementById("student-search");
    const batchFilter = document.getElementById("batch-filter");
    const rangeInput = document.getElementById("range-input");
    const applyRangeBtn = document.getElementById("apply-range-btn");
    const clearRangeBtn = document.getElementById("clear-range-btn");

    const selectAllBtn = document.getElementById("select-all-btn");
    const deselectAllBtn = document.getElementById("deselect-all-btn");
    const selectEvensBtn = document.getElementById("select-evens-btn");
    const selectOddsBtn = document.getElementById("select-odds-btn");

    const modalSelectedCount = document.getElementById("modal-selected-count");

    const rows = document.querySelectorAll(".student-row");

    function updateModalCount() {
        const count = document.querySelectorAll(".student-checkbox:checked").length;
        modalSelectedCount.textContent = count;
    }


    // -------------------------
    // Filter by batch
    // -------------------------
    batchFilter.addEventListener("change", () => {
        const batch = batchFilter.value;

        rows.forEach(row => {
            row.style.display = (batch && row.dataset.batch === batch) ? "grid" : "none";
        });

        updateModalCount();
    });


    // -------------------------
    // Search within batch
    // -------------------------
    studentSearch.addEventListener("input", () => {
        const term = studentSearch.value.toLowerCase();
        const batch = batchFilter.value;

        rows.forEach(row => {
            const match = row.textContent.toLowerCase().includes(term);
            const validBatch = (!batch || row.dataset.batch === batch);

            row.style.display = (match && validBatch) ? "grid" : "none";
        });

        updateModalCount();
    });


    // -------------------------
    // SELECT ALL / NONE
    // -------------------------
    selectAllBtn.addEventListener("click", () => {
        rows.forEach(row => {
            if (row.style.display !== "none")
                row.querySelector(".student-checkbox").checked = true;
        });
        updateModalCount();
    });

    deselectAllBtn.addEventListener("click", () => {
        rows.forEach(row => {
            if (row.style.display !== "none")
                row.querySelector(".student-checkbox").checked = false;
        });
        updateModalCount();
    });


    // -------------------------
    // Evens / Odds
    // -------------------------
    selectEvensBtn.addEventListener("click", () => {
        rows.forEach(row => {
            if (row.style.display === "none") return;
            const roll = parseInt(row.dataset.rollId);
            if (roll % 2 === 0) row.querySelector(".student-checkbox").checked = true;
        });
        updateModalCount();
    });

    selectOddsBtn.addEventListener("click", () => {
        rows.forEach(row => {
            if (row.style.display === "none") return;
            const roll = parseInt(row.dataset.rollId);
            if (roll % 2 === 1) row.querySelector(".student-checkbox").checked = true;
        });
        updateModalCount();
    });


    // -------------------------
    // Roll Range Selection
    // -------------------------
    function parseRange(str) {
        const out = new Set();
        str.split(",").forEach(part => {
            part = part.trim();
            if (!part) return;

            if (part.includes("-")) {
                let [start, end] = part.split("-").map(Number);
                for (let i = start; i <= end; i++) out.add(i.toString());
            } else {
                out.add(Number(part).toString());
            }
        });
        return [...out];
    }

    applyRangeBtn.addEventListener("click", () => {
        try {
            const ids = parseRange(rangeInput.value);
            rows.forEach(row => {
                if (row.style.display === "none") return;
                if (ids.includes(row.dataset.rollId))
                    row.querySelector(".student-checkbox").checked = true;
            });
            updateModalCount();
        } catch (e) {
            alert("Invalid range format!");
        }
    });

    clearRangeBtn.addEventListener("click", () => {
        rangeInput.value = "";
    });


    // ===============================================================
    // SAVE SELECTED STUDENTS
    // ===============================================================
    saveStudentsBtn.addEventListener("click", () => {
        const selected = [...document.querySelectorAll(".student-checkbox:checked")]
            .map(cb => cb.value);

        allowedInput.value = selected.join(",");

        closeStudentModal();
        accessModal.classList.add("active");

        updateSelectedPreview();
    });

    rows.forEach(row => {
        row.querySelector(".student-checkbox").addEventListener("change", updateModalCount);
    });


    // Initial load
    updateAccessMode();
    updateSelectedPreview();
    updateModalCount();
});


// ----------------------------------------------------
// Smooth Drawer Toggle (optional)
document.addEventListener("DOMContentLoaded", () => {
    document.querySelectorAll(".drawer-toggle").forEach(btn => {
        btn.addEventListener("click", () => {
            const parent = btn.parentElement;
            if (parent.hasAttribute("open")) {
                setTimeout(() => {
                    parent.scrollIntoView({ behavior: "smooth", block: "center" });
                }, 100);
            }
        });
    });
});

// -------- Exam Details Modal ----------
document.querySelectorAll(".open-details-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById("exam-details-modal").classList.add("active");
    });
});
document.querySelectorAll(".close-details-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById("exam-details-modal").classList.remove("active");
    });
});

// -------- Exam Access Modal ----------
document.querySelectorAll(".open-access-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById("exam-access-modal").classList.add("active");
    });
});
document.querySelectorAll(".close-access-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById("exam-access-modal").classList.remove("active");
    });
});

// -------- Questions Modal ----------
document.querySelectorAll(".open-questions-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById("questions-modal").classList.add("active");
    });
});
document.querySelectorAll(".close-questions-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        document.getElementById("questions-modal").classList.remove("active");
    });
});

// ===== Show Student Selector When "Specific Students" is selected =====
const radioSpecific = document.getElementById("specific-radio");
const selectorBox = document.getElementById("specific-student-selector");

document.querySelectorAll(".access-radio").forEach(r => {
    r.addEventListener("change", () => {
        if (radioSpecific.checked) {
            selectorBox.style.display = "block";
        } else {
            selectorBox.style.display = "none";
        }
    });
});

// ===== Open Student Selection Modal from Access Modal =====
document.getElementById("open-student-selection")?.addEventListener("click", () => {
    document.getElementById("exam-access-modal").classList.remove("active");
    document.getElementById("student-selection-modal").classList.add("active");
});


// ================================
// UPDATE SELECTED STUDENTS PREVIEW
// ================================

function updateSelectedPreview() {
    const previewBox = document.getElementById("selected-students-preview");
    const listEl = document.getElementById("selected-students-list");
    const countEl = document.getElementById("selected-count");
    const hiddenInput = document.getElementById("allowed-students-input");

    const ids = hiddenInput.value
        ? hiddenInput.value.split(",").map(x => x.trim()).filter(x => x !== "")
        : [];

    if (ids.length === 0) {
        previewBox.style.display = "none";
        return;
    }

    previewBox.style.display = "block";
    countEl.textContent = ids.length;

    listEl.innerHTML = "";

    ids.forEach(id => {
        const studentEl = document.querySelector(`[data-student-id='${id}']`);
        if (studentEl) {
            const name = studentEl.querySelector(".student-name")?.textContent || "Unknown";
            const roll = studentEl.getAttribute("data-roll-id") || "N/A";

            const li = document.createElement("li");
            li.textContent = `${name} (Roll ${roll})`;
            listEl.appendChild(li);
        }
    });
}

// Update preview whenever Access Modal opens
document.querySelectorAll(".open-access-modal").forEach(btn => {
    btn.addEventListener("click", () => {
        updateSelectedPreview();
    });
});

// Update preview after saving students
document.getElementById("modal-save-btn")?.addEventListener("click", () => {
    setTimeout(updateSelectedPreview, 300);
});

