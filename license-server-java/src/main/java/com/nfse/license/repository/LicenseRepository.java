package com.nfse.license.repository;

import com.nfse.license.model.LicenseRecord;
import org.springframework.data.jpa.repository.JpaRepository;

public interface LicenseRepository extends JpaRepository<LicenseRecord, String> {
}
