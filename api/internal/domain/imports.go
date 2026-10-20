// Package domain：校验导入格式及辅助文件是否符合对应 TaskKind 的约定；导入数据只是一种采集来源，不意味着真实现场测量。
package domain

func ValidateImport(k TaskKind, in ImportInput) *Error {
	data, err := in.Decode()
	if err != nil {
		return err
	}
	size := len(data)
	format := in.Format
	if format == "" {
		format = k.InputFormat
	}
	allowed := format == k.InputFormat
	for _, f := range k.InputFormats {
		allowed = allowed || f == format
	}
	if !allowed {
		return Fail("INVALID_PARAMETERS", "原始格式不受此TaskKind支持")
	}
	if len(in.Auxiliary) > len(k.Auxiliary) {
		return Fail("INVALID_PARAMETERS", "附件数量无效")
	}
	seen := map[string]bool{}
	for _, file := range in.Auxiliary {
		if seen[file.Filename] {
			return Fail("INVALID_PARAMETERS", "附件名称重复")
		}
		seen[file.Filename] = true
		ok := false
		for _, a := range k.Auxiliary {
			if file.Filename == a.Name && (file.Format == "" || file.Format == a.Format) {
				ok = true
			}
		}
		if !ok {
			return Fail("INVALID_PARAMETERS", "附件不在TaskKind白名单内")
		}
		b, err := (ImportInput{Filename: file.Filename, DataBase64: file.DataBase64}).Decode()
		if err != nil {
			return err
		}
		size += len(b)
	}
	for _, a := range k.Auxiliary {
		if a.Required && !seen[a.Name] {
			return Fail("INVALID_PARAMETERS", "缺少必选附件")
		}
	}
	if size > 32<<20 {
		return Fail("INVALID_PARAMETERS", "主文件和附件总大小不得超过32MiB")
	}
	return nil
}
