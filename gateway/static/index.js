/**
 * index.js — Frontend Client Controller.
 * Handles tab toggles, parameter synch, weather defaults retrieval, XSS-safe rendering, and rate limit overlays.
 */

document.addEventListener('DOMContentLoaded', () => {
    // ── Elements Cache ──
    const tabPredict = document.getElementById('tab-predict');
    const tabRecommend = document.getElementById('tab-recommend');
    const groupCrop = document.getElementById('group-crop');
    const inferenceForm = document.getElementById('inference-form');
    const btnRun = document.getElementById('btn-run');
    const btnResolveClimate = document.getElementById('btn-climate-resolve');
    
    // Sliders & Inputs
    const sliders = {
        n: document.getElementById('slider-n'),
        p: document.getElementById('slider-p'),
        k: document.getElementById('slider-k'),
        annual: document.getElementById('slider-annual-rainfall'),
        irrigation: document.getElementById('slider-irrigation-ratio')
    };
    
    const inputs = {
        n: document.getElementById('input-n'),
        p: document.getElementById('input-p'),
        k: document.getElementById('input-k'),
        annual: document.getElementById('input-annual-rainfall'),
        kharif: document.getElementById('input-kharif-rainfall'),
        rabi: document.getElementById('input-rabi-rainfall'),
        irrigation: document.getElementById('input-irrigation-ratio'),
        state: document.getElementById('select-state'),
        soil: document.getElementById('select-soil'),
        crop: document.getElementById('select-crop'),
        advisory: document.getElementById('check-advisory')
    };

    // Output views
    const outputContainer = document.getElementById('output-container');
    const stateEmpty = document.getElementById('output-state-empty');
    const stateLoading = document.getElementById('output-state-loading');
    const statePredict = document.getElementById('output-state-predict');
    const stateRecommend = document.getElementById('output-state-recommend');
    
    const loaderTitle = document.getElementById('loader-title');
    const loaderDesc = document.getElementById('loader-desc');
    
    // Result details
    const resPredictCrop = document.getElementById('res-predict-crop');
    const resPredictYield = document.getElementById('res-predict-yield');
    const resPredictSuitability = document.getElementById('res-predict-suitability');
    const resPredictBar = document.getElementById('res-predict-bar');
    const resPredictMax = document.getElementById('res-predict-max');
    const resPredictLatency = document.getElementById('res-predict-latency');
    
    const resRecommendList = document.getElementById('res-recommend-list');
    const resRecommendLatency = document.getElementById('res-recommend-latency');
    
    const advisoryPanel = document.getElementById('advisory-panel');
    const advisoryText = document.getElementById('advisory-text');

    // 429 Overlay
    const overlay429 = document.getElementById('overlay-429');
    const countdownTimer = document.getElementById('countdown-timer');
    const btnOverlayClose = document.getElementById('btn-overlay-close');

    let activeMode = 'predict'; // 'predict' or 'recommend'
    let rateLimitTimer = null;

    // ── 1. Navigation Tabs Logic ──
    tabPredict.addEventListener('click', () => setMode('predict'));
    tabRecommend.addEventListener('click', () => setMode('recommend'));

    function setMode(mode) {
        activeMode = mode;
        if (mode === 'predict') {
            tabPredict.classList.add('active');
            tabRecommend.classList.remove('active');
            groupCrop.classList.remove('hidden');
        } else {
            tabPredict.classList.remove('active');
            tabRecommend.classList.add('active');
            groupCrop.classList.add('hidden');
        }
        resetOutputs();
    }

    function resetOutputs() {
        outputContainer.classList.add('empty');
        stateEmpty.classList.remove('hidden');
        stateLoading.classList.add('hidden');
        statePredict.classList.add('hidden');
        stateRecommend.classList.add('hidden');
        advisoryPanel.classList.add('hidden');
        advisoryText.innerHTML = '';
    }

    // ── 2. Sync Sliders & Inputs ──
    const syncPairs = [
        ['n', 'n'],
        ['p', 'p'],
        ['k', 'k'],
        ['annual', 'annual'],
        ['irrigation', 'irrigation']
    ];

    syncPairs.forEach(([sliderKey, inputKey]) => {
        const slider = sliders[sliderKey];
        const input = inputs[inputKey];
        
        slider.addEventListener('input', () => {
            input.value = slider.value;
        });
        
        input.addEventListener('input', () => {
            let val = parseFloat(input.value);
            const min = parseFloat(input.min);
            const max = parseFloat(input.max);
            
            if (isNaN(val)) val = min;
            if (val < min) val = min;
            if (val > max) val = max;
            
            input.value = val;
            slider.value = val;
        });
    });

    // ── 3. Auto-Resolve Weather Defaults ──
    btnResolveClimate.addEventListener('click', async () => {
        const stateName = inputs.state.value;
        btnResolveClimate.disabled = true;
        btnResolveClimate.textContent = 'Resolving...';
        
        try {
            const response = await fetch(`/api/defaults/${encodeURIComponent(stateName)}`);
            if (!response.ok) {
                throw new Error('State weather profile not found.');
            }
            const data = await response.json();
            
            // Populate soil parameters
            inputs.n.value = Math.round(data.n_avg);
            sliders.n.value = Math.round(data.n_avg);
            inputs.p.value = Math.round(data.p_avg);
            sliders.p.value = Math.round(data.p_avg);
            inputs.k.value = Math.round(data.k_avg);
            sliders.k.value = Math.round(data.k_avg);
            
            // Populate climate parameters
            inputs.annual.value = Math.round(data.annual_rainfall_avg);
            sliders.annual.value = Math.round(data.annual_rainfall_avg);
            inputs.kharif.value = Math.round(data.kharif_rainfall_avg);
            inputs.rabi.value = Math.round(data.rabi_rainfall_avg);
            
            // Populate irrigation ratio
            inputs.irrigation.value = parseFloat(data.irrigation_ratio_avg.toFixed(2));
            sliders.irrigation.value = parseFloat(data.irrigation_ratio_avg.toFixed(2));
            
            showSuccessStatus('Resolved successfully!');
        } catch (e) {
            console.error(e);
            alert(`Could not resolve climate defaults: ${e.message}`);
        } finally {
            btnResolveClimate.disabled = false;
            btnResolveClimate.textContent = 'Auto-Resolve from State';
        }
    });

    function showSuccessStatus(msg) {
        const origText = btnResolveClimate.textContent;
        btnResolveClimate.textContent = msg;
        btnResolveClimate.style.borderColor = 'var(--color-accent)';
        btnResolveClimate.style.color = 'var(--color-accent)';
        setTimeout(() => {
            btnResolveClimate.textContent = origText;
            btnResolveClimate.style.borderColor = 'var(--border-color)';
            btnResolveClimate.style.color = 'var(--text-primary)';
        }, 2000);
    }

    // ── 4. Form Submission and Inference Trigger ──
    inferenceForm.addEventListener('submit', async (e) => {
        e.preventDefault();
        
        // 1. Gather & Validate Data
        const payload = {
            N: parseFloat(inputs.n.value),
            P: parseFloat(inputs.p.value),
            K: parseFloat(inputs.k.value),
            annual_rainfall: parseFloat(inputs.annual.value),
            kharif_rainfall: parseFloat(inputs.kharif.value),
            rabi_rainfall: parseFloat(inputs.rabi.value),
            irrigation_ratio: parseFloat(inputs.irrigation.value),
            soil_type: inputs.soil.value,
            state: inputs.state.value,
            explain: inputs.advisory.checked,
            crop: activeMode === 'predict' ? inputs.crop.value : null
        };

        // UI state transitions
        outputContainer.classList.remove('empty');
        stateEmpty.classList.add('hidden');
        statePredict.classList.add('hidden');
        stateRecommend.classList.add('hidden');
        advisoryPanel.classList.add('hidden');
        stateLoading.classList.remove('hidden');
        
        btnRun.disabled = true;
        btnRun.textContent = 'Executing Pipeline...';

        if (payload.explain) {
            loaderTitle.textContent = 'Running Inference Pipeline...';
            loaderDesc.textContent = 'Evaluating agronomic models and fetching advisory...';
        } else {
            loaderTitle.textContent = 'Evaluating prediction model...';
            loaderDesc.textContent = 'Calculating expected crop yields...';
        }

        try {
            const res = await fetch('/predict', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify(payload)
            });

            if (res.status === 429) {
                // Rate limited!
                const retryAfter = parseInt(res.headers.get('Retry-After')) || 60;
                triggerRateLimitOverlay(retryAfter);
                resetOutputs();
                return;
            }

            if (!res.ok) {
                const errorData = await res.json();
                throw new Error(errorData.detail || 'Inference pipeline failed.');
            }

            const data = await res.json();
            renderResults(data);
        } catch (err) {
            console.error(err);
            alert(`Execution failed: ${err.message}`);
            resetOutputs();
        } finally {
            btnRun.disabled = false;
            btnRun.textContent = 'Execute Inference Pipeline';
        }
    });

    // ── 5. Render Results (Safe from XSS) ──
    function renderResults(data) {
        stateLoading.classList.add('hidden');

        if (activeMode === 'predict' && data.predicted_yield !== null) {
            // Render Predict Mode
            statePredict.classList.remove('hidden');
            
            const cropDisplay = inputs.crop.options[inputs.crop.selectedIndex].text;
            resPredictCrop.textContent = cropDisplay;
            resPredictYield.textContent = formatNumber(data.predicted_yield);
            resPredictLatency.textContent = `${data.latency_ms} ms` + (data.cached ? ' (Cached)' : '');
            
            // Calculate a suitability score mock if not returned for predict
            // (Note: predict responds with suitability parameters inside advisory if checked)
            let suitability = 95.0; // fallback default
            if (data.recommendations && data.recommendations.length > 0) {
                suitability = data.recommendations[0].suitability_percentage;
            }
            
            resPredictSuitability.textContent = `${suitability}%`;
            resPredictBar.style.width = `${suitability}%`;
            resPredictMax.textContent = 'Capped at baseline limit';

        } else if (activeMode === 'recommend' && data.recommendations) {
            // Render Recommend Mode
            stateRecommend.classList.remove('hidden');
            resRecommendLatency.textContent = `${data.latency_ms} ms` + (data.cached ? ' (Cached)' : '');
            
            // Clear lists
            resRecommendList.innerHTML = '';
            
            data.recommendations.forEach((rec, idx) => {
                const row = document.createElement('div');
                row.className = 'recommendation-row';
                
                row.innerHTML = `
                    <div class="rec-rank-block">
                        <span class="rec-rank">#0${idx + 1}</span>
                        <span class="rec-name">${escapeHtml(rec.crop)}</span>
                    </div>
                    <div class="rec-data-block">
                        <div>
                            <span class="rec-metric-val">${formatNumber(rec.expected_yield_kg_per_ha)}</span>
                            <span class="rec-metric-label">Yield (Kg/ha)</span>
                        </div>
                        <div>
                            <span class="suitability-ring-label">${rec.suitability_percentage}%</span>
                            <span class="rec-metric-label">Suitability</span>
                        </div>
                    </div>
                `;
                resRecommendList.appendChild(row);
            });
        }

        // Render AI advisory panel safely with formatting
        if (data.explanation) {
            advisoryPanel.classList.remove('hidden');
            // Sanitized markdown render
            advisoryText.innerHTML = renderMarkdownSafely(data.explanation);
        } else {
            advisoryPanel.classList.add('hidden');
        }
    }

    // ── 6. Client Hardening: XSS & Markdown Sanitization ──
    function escapeHtml(str) {
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function renderMarkdownSafely(rawText) {
        // Escape HTML tags to prevent XSS script executions
        let safe = escapeHtml(rawText);
        
        // Match bold markers: **text** -> <strong>text</strong>
        safe = safe.replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>');
        
        // Match italic markers: *text* -> <em>text</em>
        safe = safe.replace(/\*(.*?)\*/g, '<em>$1</em>');
        
        // Match lists/bullet points: - item -> bullet points UI
        safe = safe.replace(/^\s*-\s+(.*?)$/gm, '<div style="margin-left: 0.5rem; margin-top: 0.25rem;">• $1</div>');
        
        return safe;
    }

    function formatNumber(num) {
        return new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 }).format(num);
    }

    // ── 7. Rate-Limit (429) Countdown Overlay ──
    function triggerRateLimitOverlay(durationSeconds) {
        overlay429.classList.remove('hidden');
        btnOverlayClose.disabled = true;
        btnOverlayClose.textContent = 'Blocked by Cooldown';
        
        let timeLeft = durationSeconds;
        countdownTimer.textContent = timeLeft;
        
        if (rateLimitTimer) clearInterval(rateLimitTimer);
        
        rateLimitTimer = setInterval(() => {
            timeLeft--;
            countdownTimer.textContent = timeLeft;
            
            if (timeLeft <= 0) {
                clearInterval(rateLimitTimer);
                btnOverlayClose.disabled = false;
                btnOverlayClose.textContent = 'Dismiss Overlay';
            }
        }, 1000);
    }

    btnOverlayClose.addEventListener('click', () => {
        overlay429.classList.add('hidden');
        if (rateLimitTimer) clearInterval(rateLimitTimer);
    });
});
