let language = "en";
let languages = ["en"];
let messages = {};
let patterns = [];
let observer;
const textOrigins = new WeakMap();
const attributeOrigins = new WeakMap();
const originalTitle = document.title;
const attributes = ["placeholder", "title", "aria-label", "alt"];

function compilePatterns()
{
    patterns = Object.keys(messages).filter((key) => /\{\d+\}/.test(key)).map((source) =>
    {
        const expression = source.split(/(\{\d+\})/).map((part) => /^\{\d+\}$/.test(part) ? "(.+?)" : part.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("");
        return {source, expression: new RegExp(`^${expression}$`, "s")};
    }).sort((first, second) => second.source.length - first.source.length);
}

function format(template, values)
{
    return template.replace(/\{(\d+)\}/g, (match, index) => values[Number(index)] ?? match);
}

export function translate(source, ...values)
{
    if (language === "en")
    {
        return format(source, values);
    }
    let row = messages[source];
    if (row === undefined && values.length === 0)
    {
        for (const item of patterns)
        {
            const match = item.expression.exec(source);
            if (match)
            {
                row = messages[item.source];
                values = match.slice(1);
                break;
            }
        }
    }
    return format(row?.[language] || source, values);
}

export function confirmTranslated(source)
{
    return window.confirm(translate(source));
}

export function currentLanguage()
{
    return language;
}

function translateWhitespace(value)
{
    const match = /^(\s*)([\s\S]*?)(\s*)$/.exec(value);
    return match ? match[1] + translate(match[2]) + match[3] : value;
}

function localizeText(node)
{
    const current = node.nodeValue;
    let origin = textOrigins.get(node);
    if (origin === undefined || current !== origin.rendered)
    {
        origin = {source: current, rendered: current};
        textOrigins.set(node, origin);
    }
    const translated = translateWhitespace(origin.source);
    origin.rendered = translated;
    if (current !== translated)
    {
        node.nodeValue = translated;
    }
}

function localizeAttributes(element)
{
    let origins = attributeOrigins.get(element);
    if (origins === undefined)
    {
        origins = new Map();
        attributeOrigins.set(element, origins);
    }
    for (const name of attributes)
    {
        if (element.hasAttribute(name) === false)
        {
            continue;
        }
        const current = element.getAttribute(name);
        let origin = origins.get(name);
        if (origin === undefined || current !== origin.rendered)
        {
            origin = {source: current, rendered: current};
            origins.set(name, origin);
        }
        const translated = translate(origin.source);
        origin.rendered = translated;
        if (current !== translated)
        {
            element.setAttribute(name, translated);
        }
    }
}

function localize(node)
{
    if (node.nodeType === Node.TEXT_NODE)
    {
        if (node.parentElement?.closest("textarea, script, style, code, pre, [contenteditable], [translate='no']") === null)
        {
            localizeText(node);
        }
        return;
    }
    if (node.nodeType !== Node.ELEMENT_NODE || node.matches("script, style, textarea, code, pre, [translate='no']"))
    {
        return;
    }
    localizeAttributes(node);
    for (const child of node.childNodes)
    {
        localize(child);
    }
}

function preferredLanguage()
{
    let cookie;
    try
    {
        cookie = document.cookie.split("; ").find((part) => part.startsWith("seatplan_lang="))?.split("=", 2)[1];
    }
    catch
    {
        cookie = null;
    }
    let stored;
    try
    {
        stored = localStorage.getItem("seatplan_lang");
    }
    catch
    {
        stored = null;
    }
    const requested = (cookie || stored || navigator.language || "en").toLowerCase();
    return languages.find((code) => code.toLowerCase() === requested) ?? languages.find((code) => code.toLowerCase() === requested.split("-")[0]) ?? "en";
}

function setLanguage(code, persist)
{
    language = languages.includes(code) ? code : "en";
    document.documentElement.lang = language;
    document.documentElement.dir = ["ar", "fa", "he", "ur"].includes(language.split("-")[0]) ? "rtl" : "ltr";
    document.title = translate(originalTitle);
    document.querySelector("#language-select").value = language;
    if (persist)
    {
        try
        {
            localStorage.setItem("seatplan_lang", language);
        }
        catch
        {
            // Cookies still persist the choice when local storage is unavailable.
        }
    }
    const path = `${document.body.dataset.base || ""}/`;
    try
    {
        document.cookie = `seatplan_lang=${encodeURIComponent(language)}; Max-Age=31536000; Path=${path}; SameSite=Lax${location.protocol === "https:" ? "; Secure" : ""}`;
    }
    catch
    {
        // Embedded test pages may not have a cookie origin.
    }
    localize(document.body);
}

export async function initializeTranslations(base, onChange = () => {})
{
    try
    {
        const response = await fetch(`${base}/api/i18n`, {credentials: "same-origin", cache: "no-store"});
        if (response.ok === false)
        {
            throw new Error(`Translation catalog returned ${response.status}.`);
        }
        const payload = await response.json();
        languages = payload.languages;
        messages = payload.messages;
        compilePatterns();
    }
    catch (error)
    {
        console.error("Translation catalog unavailable; using English.", error);
    }
    const selector = document.querySelector("#language-select");
    selector.innerHTML = languages.map((code) =>
    {
        let label = code;
        try
        {
            label = new Intl.DisplayNames([code], {type: "language"}).of(code);
        }
        catch
        {
            // A newly added language code can still be selected by its code.
        }
        const option = document.createElement("option");
        option.value = code;
        option.textContent = label;
        return option.outerHTML;
    }).join("");
    selector.parentElement.hidden = languages.length < 2;
    selector.onchange = () =>
    {
        setLanguage(selector.value, true);
        onChange(language);
    };
    setLanguage(preferredLanguage(), false);
    observer = new MutationObserver((records) =>
    {
        for (const record of records)
        {
            if (record.type === "characterData")
            {
                localize(record.target);
            }
            else
            {
                for (const node of record.addedNodes)
                {
                    localize(node);
                }
            }
        }
    });
    observer.observe(document.body, {subtree: true, childList: true, characterData: true});
}
