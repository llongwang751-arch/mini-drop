/** 独立的公开阅读入口；浏览教程不恢复业务会话，也不请求采集或诊断接口。 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties } from "react";
import { Link, useLocation, useParams } from "react-router-dom";
import {
  ArrowLeft,
  ArrowRight,
  ArrowUpRight,
  BookOpen,
  Clock,
  Drop,
  List,
  Moon,
  SidebarSimple,
  Sun,
  X,
} from "@phosphor-icons/react";
import { chapters, chapterUrl } from "./chapters";
import BookContent from "./BookContent";
import { parseBookMarkdown } from "./markdown";
import { BOOK_PREFERENCES_KEY, readBookPreferences } from "./preferences";
import "./book.css";

const repository = "https://github.com/llongwang751-arch/mini-drop";

function Pipeline() {
  return (
    <figure className="book-pipeline">
      <ol>
        {[
          ["提出问题", "React · Web"],
          ["安排任务", "Go · API"],
          ["采集现场", "C++ · Agent"],
          ["解析数据", "Python · Analyzer"],
          ["读懂结果", "React · Web"],
        ].map(([title, description]) => (
          <li key={title}>
            <strong>{title}</strong>
            <span>{description}</span>
          </li>
        ))}
      </ol>
      <figcaption>
        先顺着一条任务走完。AI 诊断在这条链路之上读取证据。
      </figcaption>
    </figure>
  );
}

export default function Book() {
  const { chapterSlug } = useParams();
  const location = useLocation();
  const chapter = chapters.find((item) => `chapter${item.id}` === chapterSlug);
  const [preferences, setPreferences] = useState(readBookPreferences);
  const [focused, setFocused] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const [activeSection, setActiveSection] = useState("");
  const [storageAvailable, setStorageAvailable] = useState(true);
  const [loadedContent, setLoadedContent] = useState<{
    id: number;
    text: string;
    failed?: boolean;
  }>();
  const article = useRef<HTMLElement>(null);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const menuClose = useRef<HTMLButtonElement>(null);
  const chapterContent =
    chapter?.content ??
    (loadedContent?.id === chapter?.id ? loadedContent?.text : "") ??
    "";
  const parsed = useMemo(
    () => parseBookMarkdown(chapterContent),
    [chapterContent],
  );
  const index = chapter ? chapters.indexOf(chapter) : -1;
  const previous = chapters[index - 1];
  const next = chapters[index + 1];

  useEffect(() => {
    if (!chapter?.loadContent) return;
    let active = true;
    void chapter
      .loadContent()
      .then((text) => {
        if (active) setLoadedContent({ id: chapter.id, text });
      })
      .catch(() => {
        if (active)
          setLoadedContent({ id: chapter.id, text: "", failed: true });
      });
    return () => {
      active = false;
    };
  }, [chapter]);

  useEffect(() => {
    try {
      localStorage.setItem(BOOK_PREFERENCES_KEY, JSON.stringify(preferences));
      setStorageAvailable(true);
    } catch {
      setStorageAvailable(false);
    }
  }, [preferences]);

  useEffect(() => {
    const oldTitle = document.title;
    document.title = chapter
      ? `${chapter.title} · Drop 项目教程`
      : "章节不存在 · Drop 项目教程";
    return () => {
      document.title = oldTitle;
    };
  }, [chapter]);

  useEffect(() => {
    setMenuOpen(false);
    setActiveSection(parsed.sections[0]?.id || "");
    if (!location.hash) {
      window.scrollTo({ top: 0, behavior: "instant" });
      return;
    }
    const frame = requestAnimationFrame(() => {
      document.getElementById(location.hash.slice(1))?.scrollIntoView();
    });
    return () => cancelAnimationFrame(frame);
  }, [location.pathname, location.hash, parsed]);

  useEffect(() => {
    const headings = article.current?.querySelectorAll("h2[id]");
    if (!headings?.length) return;
    // 根据当前视口中的标题定位目录，不把滚动位置写成“已经学会”的进度。
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries
          .filter((entry) => entry.isIntersecting)
          .sort(
            (left, right) =>
              left.boundingClientRect.top - right.boundingClientRect.top,
          );
        if (visible[0]) setActiveSection(visible[0].target.id);
      },
      { rootMargin: "-90px 0px -55% 0px", threshold: 0 },
    );
    headings.forEach((heading) => observer.observe(heading));
    return () => observer.disconnect();
  }, [parsed]);

  useEffect(() => {
    if (!menuOpen) return;
    const previousOverflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    menuClose.current?.focus();
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") {
        setMenuOpen(false);
        menuTrigger.current?.focus();
      }
      if (event.key === "Tab") {
        const links = document.querySelectorAll<HTMLElement>(
          ".book-sidebar button, .book-sidebar a[href]",
        );
        const first = links[0];
        const last = links[links.length - 1];
        if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last?.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first?.focus();
        }
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => {
      document.body.style.overflow = previousOverflow;
      document.removeEventListener("keydown", closeOnEscape);
    };
  }, [menuOpen]);

  const closeMenu = () => {
    setMenuOpen(false);
    menuTrigger.current?.focus();
  };
  const currentPosition = Math.max(
    0,
    parsed.sections.findIndex((section) => section.id === activeSection),
  );

  return (
    <div
      className={`drop-book${focused ? " is-focused" : ""}`}
      data-theme={preferences.theme}
      style={
        { "--book-font-size": `${preferences.fontSize}px` } as CSSProperties
      }
    >
      <a className="book-skip" href="#book-main">
        跳到正文
      </a>
      <header className="book-header">
        <Link
          className="book-brand"
          to={chapterUrl(1)}
          aria-label="Drop 项目教程首页"
        >
          <Drop size={27} weight="fill" aria-hidden="true" />
          <span>
            drop.<small>项目教程</small>
          </span>
        </Link>
        <nav className="book-header-links" aria-label="教程主导航">
          <Link to={chapterUrl(1)}>从头开始</Link>
          <Link to={chapterUrl(11)}>动手实战</Link>
          <a
            className="book-repo-link"
            href={repository}
            target="_blank"
            rel="noreferrer"
          >
            GitHub <ArrowUpRight size={14} aria-hidden="true" />
          </a>
          <Link to="/">
            工作台 <ArrowUpRight size={14} aria-hidden="true" />
          </Link>
        </nav>
        <button
          className="book-icon-button"
          type="button"
          onClick={() =>
            setPreferences((value) => ({
              ...value,
              theme: value.theme === "light" ? "dark" : "light",
            }))
          }
          aria-label={
            preferences.theme === "light" ? "切换深色模式" : "切换浅色模式"
          }
        >
          {preferences.theme === "light" ? (
            <Moon size={19} />
          ) : (
            <Sun size={19} />
          )}
        </button>
        <button
          ref={menuTrigger}
          type="button"
          className="book-icon-button book-contents-button"
          aria-label="打开章节目录"
          aria-expanded={menuOpen}
          aria-controls="book-chapters"
          onClick={() => {
            setFocused(false);
            setMenuOpen(true);
          }}
        >
          <List size={22} />
        </button>
      </header>
      {menuOpen && (
        <button
          type="button"
          className="book-mobile-backdrop"
          aria-label="关闭章节目录"
          tabIndex={-1}
          onClick={closeMenu}
        />
      )}
      <div className="book-layout">
        <aside
          id="book-chapters"
          className={`book-sidebar${menuOpen ? " is-open" : ""}`}
          aria-label="全书章节"
        >
          <button
            ref={menuClose}
            type="button"
            className="book-icon-button book-mobile-close"
            aria-label="关闭章节目录"
            onClick={closeMenu}
          >
            <X size={22} />
          </button>
          <div className="book-sidebar-intro">
            <BookOpen size={17} aria-hidden="true" />
            <span>读懂 Drop，动手改它</span>
          </div>
          <nav aria-label="章节导航">
            {chapters.map((item, position) => (
              <div key={item.id}>
                {(position === 0 ||
                  chapters[position - 1].part !== item.part) && (
                  <p className="book-part-label">{item.part}</p>
                )}
                <Link
                  className="book-chapter-link"
                  to={chapterUrl(item.id)}
                  aria-current={item.id === chapter?.id ? "page" : undefined}
                >
                  <span className="book-chapter-number">
                    {String(item.id).padStart(2, "0")}
                  </span>
                  <span className="book-chapter-label">
                    <strong>{item.title}</strong>
                    <small>{item.subtitle}</small>
                  </span>
                </Link>
              </div>
            ))}
          </nav>
          <div className="book-sidebar-footer">
            <p>先看见一条任务如何完成，再理解每一层为什么这样写。</p>
            <a
              href={`${repository}/blob/master/docs/CODE_READING_GUIDE.md`}
              target="_blank"
              rel="noreferrer"
            >
              源码阅读路线 <ArrowUpRight size={13} aria-hidden="true" />
            </a>
          </div>
        </aside>
        {chapter ? (
          <>
            <main className="book-main" id="book-main" tabIndex={-1}>
              <div className="book-chapter-meta">
                <span>
                  全书目录 / 第 {String(chapter.id).padStart(2, "0")} 章
                </span>
                <span className="book-meta-item">
                  <Clock size={14} aria-hidden="true" />约{" "}
                  {chapter.readingMinutes} 分钟 · 含动手练习
                </span>
              </div>
              <header className="book-chapter-header">
                <p className="book-eyebrow">
                  第 {String(chapter.id).padStart(2, "0")} 章 · {chapter.part}
                </p>
                <h1>{chapter.title}</h1>
                <p className="book-subtitle">{chapter.subtitle}</p>
                <p className="book-lead">{chapter.lead}</p>
              </header>
              <div
                className="book-reading-controls"
                role="group"
                aria-label="阅读设置"
              >
                <span>字号</span>
                <button
                  type="button"
                  aria-label="缩小字号"
                  disabled={preferences.fontSize <= 16}
                  onClick={() =>
                    setPreferences((value) => ({
                      ...value,
                      fontSize: Math.max(16, value.fontSize - 2),
                    }))
                  }
                >
                  A−
                </button>
                <output aria-label="当前字号">{preferences.fontSize}</output>
                <button
                  type="button"
                  aria-label="放大字号"
                  disabled={preferences.fontSize >= 22}
                  onClick={() =>
                    setPreferences((value) => ({
                      ...value,
                      fontSize: Math.min(22, value.fontSize + 2),
                    }))
                  }
                >
                  A+
                </button>
                <button
                  type="button"
                  aria-pressed={focused}
                  onClick={() => setFocused((value) => !value)}
                >
                  <SidebarSimple size={16} aria-hidden="true" />
                  {focused ? "退出专注" : "专注阅读"}
                </button>
              </div>
              {!storageAvailable && (
                <p className="book-storage-note" role="status">
                  浏览器无法保存阅读设置，本次阅读仍可调整。
                </p>
              )}
              <div className="book-objective">
                <strong>阅读前</strong>
                <p>{chapter.prerequisites}</p>
              </div>
              <details
                className="book-inline-toc"
                key={`contents-${chapter.id}`}
              >
                <summary>本章内容 · {parsed.sections.length} 节</summary>
                <nav aria-label="窄屏本章目录">
                  {parsed.sections.map((section) => (
                    <a key={section.id} href={`#${section.id}`}>
                      {section.title}
                    </a>
                  ))}
                </nav>
              </details>
              {chapter.id === 1 && <Pipeline />}
              <article
                className="book-article"
                ref={article}
                aria-label={`${chapter.title}正文`}
                key={chapter.id}
              >
                {loadedContent?.id === chapter.id && loadedContent.failed ? (
                  <p role="alert">章节载入失败，请刷新后重试。</p>
                ) : !chapterContent ? (
                  <p role="status">正在载入本章正文…</p>
                ) : (
                  <BookContent tree={parsed.tree} />
                )}
              </article>
              <section className="book-source-section" aria-label="对照源码">
                <details>
                  <summary>
                    对照源码与项目文档 · {chapter.sources.length} 个入口
                  </summary>
                  <p>
                    这里链接到仓库文件。阅读与修改时，以你当前检出的版本为准。
                  </p>
                  <ul>
                    {chapter.sources.map((source) => (
                      <li key={source.path}>
                        {source.local ? (
                          <div className="book-local-source">
                            <strong>{source.label}</strong>
                            <code>{source.path}</code>
                            <span>配套代码位于本地项目目录</span>
                          </div>
                        ) : (
                          <a
                            href={`${repository}/blob/master/${source.path}`}
                            target="_blank"
                            rel="noreferrer"
                          >
                            <strong>{source.label}</strong>
                            <code>{source.path}</code>
                            <ArrowUpRight size={14} aria-hidden="true" />
                          </a>
                        )}
                      </li>
                    ))}
                  </ul>
                </details>
              </section>
              <nav className="book-page-navigation" aria-label="上一章与下一章">
                {previous ? (
                  <Link to={chapterUrl(previous.id)}>
                    <ArrowLeft size={17} aria-hidden="true" />
                    <span>
                      <small>上一章</small>
                      <strong>{previous.title}</strong>
                    </span>
                  </Link>
                ) : (
                  <span />
                )}
                {next ? (
                  <Link to={chapterUrl(next.id)}>
                    <span>
                      <small>下一章</small>
                      <strong>{next.title}</strong>
                    </span>
                    <ArrowRight size={17} aria-hidden="true" />
                  </Link>
                ) : (
                  <Link to={chapterUrl(1)}>
                    <span>
                      <small>重新串起整条链路</small>
                      <strong>回到第一章</strong>
                    </span>
                    <ArrowRight size={17} aria-hidden="true" />
                  </Link>
                )}
              </nav>
            </main>
            <aside className="book-toc" aria-label="本章内容">
              <p>本章内容</p>
              <nav aria-label="本章目录">
                {parsed.sections.map((section) => (
                  <a
                    key={section.id}
                    className={`book-toc-link${activeSection === section.id ? " active" : ""}`}
                    aria-current={
                      activeSection === section.id ? "location" : undefined
                    }
                    href={`#${section.id}`}
                    onClick={() => setActiveSection(section.id)}
                  >
                    {section.title}
                  </a>
                ))}
              </nav>
              {parsed.sections.length > 0 && (
                <div className="book-progress">
                  <progress
                    aria-label="本章阅读位置"
                    value={currentPosition + 1}
                    max={parsed.sections.length || 1}
                  />
                  <span>
                    第 {currentPosition + 1} / {parsed.sections.length} 节
                  </span>
                </div>
              )}
              <a className="book-back-top" href="#book-main">
                回到顶部 ↑
              </a>
            </aside>
          </>
        ) : (
          <main className="book-main book-empty" id="book-main">
            <h1>这一章还不存在</h1>
            <p>全书有 {chapters.length} 章，可以从左侧目录选择。</p>
            <Link to={chapterUrl(1)}>
              从第一章开始 <ArrowRight size={17} aria-hidden="true" />
            </Link>
          </main>
        )}
      </div>
    </div>
  );
}
