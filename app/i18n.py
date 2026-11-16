CATALOGS={
'en':{'dashboard':'Dashboard','people':'People','organization':'Organization','leave':'Leave','workforce':'Workforce','talent':'Talent','payroll':'Payroll','documents':'Documents','recruitment':'Recruitment','onboarding':'Onboarding','reports':'Reports','settings':'Settings','sign_out':'Sign out','skip_content':'Skip to main content','language':'Language'},
'hi':{'dashboard':'डैशबोर्ड','people':'लोग','organization':'संगठन','leave':'अवकाश','workforce':'कार्यबल','talent':'प्रतिभा','payroll':'वेतन','documents':'दस्तावेज़','recruitment':'भर्ती','onboarding':'ऑनबोर्डिंग','reports':'रिपोर्ट','settings':'सेटिंग्स','sign_out':'साइन आउट','skip_content':'मुख्य सामग्री पर जाएँ','language':'भाषा'}
}
def translate(locale):
    catalog=CATALOGS.get(locale,CATALOGS['en'])
    return lambda key:catalog.get(key,CATALOGS['en'].get(key,key))
