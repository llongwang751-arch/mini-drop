// 显式合成的模型/工具 JSON 语料；这里只核验确定性的证据协议，不调用付费模型。
package service

import (
	"bytes"
	"encoding/json"
	"testing"
)

func FuzzDiagnosisObservationObject(f *testing.F) {
	for _, seed := range []string{`{"total_weight":9007199254740991}`, `{"x":[null,true,"测试",1.01]}`, `{}`, `null`, `{} {}`, `{"x":1e999999}`, `{"x":true,"x":false}`} {
		f.Add([]byte(seed))
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		if len(raw) > 70<<10 {
			return
		}
		value, err := diagnosisDecodeObject(raw)
		if err != nil {
			return
		}
		if value == nil || len(raw) == 0 || len(raw) > 64<<10 || !json.Valid(raw) {
			t.Fatal("observation escaped object/byte boundary")
		}
		// 成功输入只能是一份完整对象，尾随第二个值绝不能被忽略。
		withTail := append(append([]byte{}, raw...), []byte(" null")...)
		if _, err := diagnosisDecodeObject(withTail); err == nil {
			t.Fatal("trailing JSON value accepted")
		}
		encoded, err := json.Marshal(value)
		if err != nil {
			t.Fatal(err)
		}
		// JSON 编码可把 HTML 字符转义为更长的序列；重新编码也必须遵守输入字节上限。
		if len(encoded) > 64<<10 {
			return
		}
		again, err := diagnosisDecodeObject(encoded)
		if err != nil || again == nil {
			t.Fatal("valid observation did not survive canonical round trip")
		}
	})
}

func FuzzDiagnosisExactScalar(f *testing.F) {
	for _, seed := range []string{`70.000000000000000001`, `9007199254740991`, `1e-100`, `1e101`, `true`, `"中文"`, `null`, `{}`, `1 2`} {
		f.Add([]byte(seed))
	}
	f.Fuzz(func(t *testing.T, raw []byte) {
		if len(raw) > 512 {
			return
		}
		decoder := json.NewDecoder(bytes.NewReader(raw))
		decoder.UseNumber()
		var expected any
		if decoder.Decode(&expected) != nil || !json.Valid(raw) {
			return
		}
		if !diagnosisSameScalar(expected, raw) {
			return
		}
		if len(raw) > 256 {
			t.Fatal("scalar byte bound escaped")
		}
		if diagnosisSameScalar(expected, append(append([]byte{}, raw...), []byte(" false")...)) {
			t.Fatal("scalar trailing token accepted")
		}
		// 数字比较必须使用精确有理数：舍入成同一 float64 不能让不同值相等。
		if number, ok := expected.(json.Number); ok {
			left, valid := diagnosisNumeric(number)
			if !valid || left == nil {
				t.Fatal("accepted scalar has no bounded exact numeric representation")
			}
			canonical := json.RawMessage(number.String())
			if !diagnosisSameScalar(expected, canonical) {
				t.Fatal("exact number changed during round trip")
			}
		}
	})
}
